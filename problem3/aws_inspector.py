import boto3
import sys,os,json,datetime,argparse
from botocore.exceptions import ClientError, NoCredentialsError, EndpointConnectionError

def time_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def err(msg):
    print(f"{time_now()} [ERROR] {msg}", file=sys.stderr)

def warn(msg):
    print(f"{time_now()} [WARNING] {msg}", file=sys.stderr)

def timeout(e):
    if isinstance(e, EndpointConnectionError):
        return True
    try:
        code=e.response.get("Error",{}).get("Code","")
        return code in ["RequestTimeout","RequestTimeoutException","Throttling","ThrottlingException","ThrottledException"]
    except Exception:
        return False
def call_limit(fn,label):
    try:
        return fn()
    except Exception as e:
        if timeout(e):
            warn(f"{label} call timed out: {e}")
            try:
                return fn()
            except Exception as e2:
                warn(f"{label} retry call failed: {e2}")
                return None
        raise

def access_den(e):
    try:
        code=e.response.get("Error",{}).get("Code","")
        return code in ["AccessDenied","AccessDeniedException","UnauthorizedOperation"]
    except Exception:
        return False
def valid_region(session,region):
    try:
        client= session.client("ec2",region_name= region)
        resp= client.describe_regions(AllRegions= True)
        regs= [r.get("RegionName","") for r in resp.get("Regions",[])]
        return region in regs
    except Exception as e:
        err(f"region validation error: {e}")
        return False

def creds_aws(region):
    try:
        session=boto3.Session(region_name= region)
        sts= session.client('sts')
        ident=sts.get_caller_identity()
        return session, ident
    except (NoCredentialsError,ClientError) as e:
        err(f"authentication failed: {e}")
        return None, None
    except Exception as e:
        err(f"authentication error: {e}")  
        return None, None

def args():
    ap=argparse.ArgumentParser()
    ap.add_argument('--region',default='us-east-1')
    ap.add_argument('--output',default=None)
    ap.add_argument('--format',default='json',choices=['json','table'])
    return ap.parse_args()


def iam(session):
    client= session.client('iam')
    users=[]
    try:
        pag= client.get_paginator('list_users')
        for page in pag.paginate():
            for user in page.get('Users',[]):
                username= user.get('UserName','')
                userarn= user.get('Arn','')
                userid= user.get('UserId','')
                cd= user.get('CreateDate')
                create_date= cd.isoformat().replace('+00:00', 'Z') if cd else None

                last_activity= None
                try:
                    u2= client.get_user(UserName=username)
                    lau= u2.get("User", {}).get("PasswordLastUsed")
                    if lau:
                        last_activity= lau.isoformat().replace('+00:00', 'Z')
                except ClientError as e:
                    if access_den(e):
                        err(f"iam access error for {username}: {e}")
                    else:
                        err(f"iam get_user error {username}: {e}")
                attached_policies= []
                try:
                    pag2= client.get_paginator('list_attached_user_policies')
                    for page2 in pag2.paginate(UserName=username):
                        for p in page2.get('AttachedPolicies', []):
                            attached_policies.append({
                                'policy_name': p.get('PolicyName',''),
                                'policy_arn': p.get('PolicyArn','')
                            })
                except ClientError as e:
                    if access_den(e):
                        err(f"iam access error for {username}: {e}")
                    else:
                        err(f"iam list_attached_user_policies error for {username}: {e}")
                users.append({
                    'username': username,
                    'user_id': userid,
                    'arn': userarn,
                    'create_date': create_date,
                    'last_activity': last_activity,
                    'attached_policies': attached_policies
                })
        return users
    except ClientError as e:  
        if access_den(e):
            err(f"iam access error: {e}")
            return []
        else:  
            err(f"iam list_users error: {e}")
        return []


def ec2_inst(session,region):
    client= session.client("ec2",region_name=region)
    instances=[]
    try:
        pag= client.get_paginator("describe_instances")
        for page in pag.paginate():
            for res in page.get("Reservations",[]):
                for inst in res.get("Instances",[]):
                    instance_id= inst.get("InstanceId","")
                    instance_type= inst.get("InstanceType","")
                    state= inst.get("State",{}).get("Name","")
                    public_ip= inst.get("PublicIpAddress",None)
                    private_ip= inst.get("PrivateIpAddress",None)
                    az= inst.get("Placement",{}).get("AvailabilityZone","")
                    launch_time= inst.get("LaunchTime",None)
                    launch_date= launch_time.isoformat().replace('+00:00', 'Z') if launch_time else None
                    ami_id= inst.get("ImageId","")
                    ami_name= None
                    if ami_id:
                        try:
                            img= client.describe_images(ImageIds=[ami_id])
                            images= img.get("Images",[])
                            if images:
                                ami_name= images[0].get("Name")
                        except ClientError as e:
                            if access_den(e):
                                err(f"ec2 access error for AMI {ami_id}: {e}")
                            else:
                                err(f"ec2 describe_images error for AMI {ami_id}: {e}")

                    security_groups= []
                    for sg in inst.get("SecurityGroups",[]):
                        sg_id= sg.get("GroupId","")
                        if sg_id:
                            security_groups.append(sg_id)
                    tags={}
                    for tag in inst.get("Tags",[]):
                        key= tag.get("Key")
                        value= tag.get("Value")
                        if key is not None and value is not None:
                            tags[key]= value
                    instances.append({
                        "instance_id": instance_id,
                        "instance_type": instance_type,
                        "state": state,
                        "public_ip": public_ip,
                        "private_ip": private_ip,
                        "availability_zone": az,
                        "launch_time": launch_date,
                        "ami_id": ami_id,
                        "ami_name": ami_name,
                        "security_groups": security_groups,
                        "tags": tags
                    })  
        return instances
    except ClientError as e:
        if access_den(e):
            err(f"ec2 access error: {e}")
        else:
            err(f"ec2 describe_instances error: {e}")
        return []
    
def s3_helper(client,bucket_name):
    count= 0
    size= 0
    try:
        pag= client.get_paginator("list_objects_v2")
        for page in pag.paginate(Bucket=bucket_name):
            for obj in page.get("Contents",[]):
                count+= 1
                size+= obj.get("Size",0)
        return count, size
    except ClientError as e:
        if access_den(e):
            err(f"s3 access error for bucket {bucket_name}: {e}")

        else:
            err(f"s3 list_objects_v2 error for bucket {bucket_name}: {e}")
        return 0, 0
                
                    
def s3_buckets(session,region):
    client= session.client("s3")
    buckets=[]
    try:
        resp= client.list_buckets()
        for b in resp.get("Buckets",[]):
            bucket_name= b.get("Name","")
            try:
                bucket_location= client.get_bucket_location(Bucket=bucket_name)
                loc= bucket_location.get("LocationConstraint")
                bucket_region= loc if loc else "us-east-1"
            except ClientError:
                continue
            if bucket_region != region:
                continue
            creation_date= b.get("CreationDate",None)
            creation_date_str= creation_date.isoformat().replace('+00:00', 'Z') if creation_date else None
            obj_count, size_bytes= s3_helper(client,bucket_name)
            buckets.append({
                "bucket_name": bucket_name,
                "creation_date": creation_date_str,
                "region": bucket_region,
                "object_count": obj_count,
                "size_bytes": size_bytes
            })
        return buckets
    except ClientError as e:
        if access_den(e):
            err(f"s3 access error: {e}")
        else:
            err(f"s3 list_buckets error: {e}")
        return []


def secg_helper(sg, direction):
    rules= []
    for p in sg or []:
        prot= p.get("IpProtocol","")
        fp= p.get("FromPort") 
        tp= p.get("ToPort")
        if prot == "all" or (fp is None) or (tp is None):
            port_range= "all"
        else:
            port_range= f"{fp}-{tp}" if fp != tp else str(fp)
        targets= []
        targets+= [r.get("CidrIp") for r in p.get("IpRanges",[]) if r.get("CidrIp")]
        targets+= [r.get("CidrIpv6") for r in p.get("Ipv6Ranges",[]) if r.get("CidrIpv6")]
        targets+= [f"sg:{r.get('GroupId')}" for r in p.get("UserIdGroupPairs",[]) if r.get("GroupId")]
        if not targets:
            targets.append("none")
        for t in targets:
            if direction == "inbound":
                rules.append({
                    "protocol": prot,
                    "port_range": port_range,
                    "source": t
                })
            else:
                rules.append({
                    "protocol": prot,
                    "port_range": port_range,
                    "destination": t
                })
    return rules

def security_groups(session,region):
    client= session.client("ec2",region_name=region)
    sgs=[]
    try:
        pag= client.get_paginator("describe_security_groups")
        for page in pag.paginate():
            for sg in page.get("SecurityGroups",[]):
                sg_id= sg.get("GroupId","")
                sg_name= sg.get("GroupName","")
                description= sg.get("Description","")
                vpc_id= sg.get("VpcId",None)
                sgs.append({
                    "group_id": sg_id,
                    "group_name": sg_name,
                    "description": description,
                    "vpc_id": vpc_id,
                    "inbound_rules": secg_helper(sg.get("IpPermissions",[]),"inbound"),
                    "outbound_rules": secg_helper(sg.get("IpPermissionsEgress",[]),"outbound")
                })
        return sgs
    except ClientError as e:
        if access_den(e):
            err(f"ec2 access error for security groups: {e}")
        else:
            err(f"ec2 describe_security_groups error: {e}")
        return []
    
def out_json(data):
    account_info= {
        "account_id": data.get("account_id"),
        "user_arn": data.get("user_arn"),
        "region": data.get("region"),
        "scan_timestamp": data.get("scan_timestamp")
    }
    resources= {
        "iam_users": data.get("iam_users",[]),
        "ec2_instances": data.get("ec2_instances",[]),
        "s3_buckets": data.get("s3_buckets",[]),
        "security_groups": data.get("security_groups",[])
    }
    summary= {
        "total_users": len(resources["iam_users"]),
        "running_instances": len([i for i in resources["ec2_instances"] if i.get("state") == "running"]),
        "total_buckets": len(resources["s3_buckets"]),
        "security_groups": len(resources["security_groups"])
    }
    return {
        "account_info": account_info,
        "resources": resources,
        "summary": summary
    }



###used chatGPT to help with table functions
def _fmt(s, n):
    s= "" if s is None else str(s)
    if len(s) > n:
        return s[:n-3] + "..."
    return s

def _pad(s, n):
    s= "" if s is None else str(s)
    return s + (" " * max(0, n - len(s)))

def _widths(headers, rows):
    ws= [len(h) for h in headers]
    for r in rows:
        for i,v in enumerate(r):
            ws[i]= max(ws[i], len(str(v)))
    return ws

def _print_table(title, headers, rows):
    print(title)
    if not rows:
        print("(none)")
        print("")
        return

    ws= _widths(headers, rows)

    h= []
    for i,hd in enumerate(headers):
        h.append(_pad(hd, ws[i]))
    print("  ".join(h))

    sep= []
    for w in ws:
        sep.append("-" * w)
    print("  ".join(sep))

    for r in rows:
        line= []
        for i,v in enumerate(r):
            line.append(_pad(v, ws[i]))
        print("  ".join(line))
    print("")

def out_table(data):
    # header
    print(f"AWS Account: {data.get('account_id','-')} ({data.get('region','-')})")
    ts= data.get("scan_timestamp","-")
    print(f"Scan Time: {str(ts).replace('T',' ')[:19]} UTC")
    print("")

    # IAM
    users= data.get("iam_users",[])
    rows= []
    for u in users:
        rows.append([
            _fmt(u.get("username",""), 28),
            _fmt((u.get("create_date","") or "")[:10], 10),
            _fmt((u.get("last_activity","-") or "-")[:10], 10),
            str(len(u.get("attached_policies",[]) or []))
        ])
    _print_table(f"IAM USERS ({len(users)} total)",
                 ["Username","Create Date","Last Activity","Policies"],
                 rows)

    # EC2
    inst= data.get("ec2_instances",[])
    run_ct= 0
    for i in inst:
        if i.get("state") == "running":
            run_ct += 1

    rows= []
    for it in inst:
        rows.append([
            _fmt(it.get("instance_id",""), 22),
            _fmt(it.get("instance_type",""), 10),
            _fmt(it.get("state",""), 10),
            _fmt(it.get("public_ip","-") or "-", 16),
            _fmt((it.get("launch_time","-") or "-")[:16].replace("T"," "), 16)
        ])
    _print_table(f"EC2 INSTANCES ({run_ct} running, {len(inst) - run_ct} stopped)",
                 ["Instance ID","Type","State","Public IP","Launch Time"],
                 rows)

    # S3
    buckets= data.get("s3_buckets",[])
    rows= []
    for b in buckets:
        size_mb= (b.get("size_bytes",0) or 0) / (1024.0 * 1024.0)
        rows.append([
            _fmt(b.get("bucket_name",""), 35),
            _fmt(b.get("region",""), 10),
            _fmt((b.get("creation_date","-") or "-")[:10], 10),
            str(b.get("object_count",0)),
            f"~{size_mb:.1f}"
        ])
    _print_table(f"S3 BUCKETS ({len(buckets)} total)",
                 ["Bucket Name","Region","Created","Objects","Size (MB)"],
                 rows)

    # SG
    sgs= data.get("security_groups",[])
    rows= []
    for sg in sgs:
        rows.append([
            _fmt(sg.get("group_id",""), 14),
            _fmt(sg.get("group_name",""), 18),
            _fmt(sg.get("vpc_id","-") or "-", 14),
            str(len(sg.get("inbound_rules",[]) or []))
        ])
    _print_table(f"SECURITY GROUPS ({len(sgs)} total)",
                 ["Group ID","Name","VPC ID","Inbound Rules"],
                 rows)
    
###########################################################



def output_results(data, output_file, output_format):
    if output_format == "json":
        out= out_json(data)
        out_str= json.dumps(out, indent=2)
        if output_file:
            try:
                with open(output_file,"w") as f:
                    f.write(out_str)
            except Exception as e:
                err(f"could not write output file {output_file}: {e}")
                sys.exit(1)
        else:
            print(out_str)
        return
    
    if output_file: #table
        try:
            t=sys.stdout
            with open(output_file,"w") as f:
                sys.stdout= f
                out_table(data)
            sys.stdout= t
        except Exception as e:
            try:
                sys.stdout= t
            except Exception:
                pass
            err(f"could not write output file {output_file}: {e}")
            sys.exit(1)
    else:
        out_table(data)










a= args()
session, identity= creds_aws(a.region)
if not session or not identity:
    err("Could not authenticate to AWS. Exiting.")
    sys.exit(1)
if not valid_region(session,a.region):
    err(f"Invalid region specified: {a.region}. Exiting.")
    sys.exit(1)
account_id= identity.get("Account","")
user_arn= identity.get("Arn","")
iam_users= call_limit(lambda: iam(session), "iam")
if iam_users is None:
    iam_users= []

ec2_instances= call_limit(lambda: ec2_inst(session,a.region), "ec2_instances")
if ec2_instances is None:
    ec2_instances= []
s3_list= call_limit(lambda: s3_buckets(session,a.region), "s3_buckets")
if s3_list is None:
    s3_list= []

sec_g= call_limit(lambda: security_groups(session,a.region), "security_groups")
if sec_g is None:
    sec_g= []

output_data= {
    "account_id": account_id,
    "user_arn": user_arn,
    "region": a.region,
    "scan_timestamp": time_now(),
    "iam_users": iam_users,
    "ec2_instances": ec2_instances,
    "s3_buckets": s3_list,
    "security_groups": sec_g
}
output_results(output_data,a.output,a.format)