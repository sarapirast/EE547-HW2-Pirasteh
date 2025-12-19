import sys, os,json,re
import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.parse

def time_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def log_line(path, status, extra=""):
    ts= time_now()
    phrase= {200:"OK", 400:"Bad Request", 404:"Not Found", 500:"Internal Server Error"}.get(status, "")
    msg= f"[{ts}] GET {path} - {status} {phrase}"
    if extra:
        msg+=f" ({extra})"
    print(msg, flush=True)

log=[]

def load_files(path):
    pa= os.path.abspath(path)
    try:
        with open(pa, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        log.append(f"ERROR file not found: {pa}")
        return None
    except json.JSONDecodeError as e:
        log.append(f"ERROR bad JSON ({pa}): {e}")
        return None
    except Exception as e:
        log.append(f"ERROR reading {pa}: {type(e).__name__}: {e}")
        return None


paper= 'sample_data/papers.json'
corpus= 'sample_data/corpus_analysis.json'

papers= load_files(paper)
corpuses= load_files(corpus)

if log:
    print("\n".join(f"[{time_now()}] {m}" for m in log), file=sys.stderr)

if corpuses and isinstance(corpuses, dict) and "top_50_words" in corpuses:
    corpuses['top_10_words'] = (corpuses.pop('top_50_words', [])[:10])
papers= papers   if isinstance(papers, list) else []
corpuses= corpuses if isinstance(corpuses, dict) else {}

idd= {p.get("arxiv_id"): p for p in papers if p.get("arxiv_id")}
paper_path= {f"/papers/{urllib.parse.quote(pid)}" for pid in idd.keys()}

server_class= HTTPServer
handler_class= BaseHTTPRequestHandler

class ArxivHandler(BaseHTTPRequestHandler): ##must be subclass

    def json_response(self,status,data):
        response = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def err(self,message):
        return {"error": message}
    def log_message(self, format, *args):
        return

    def do_GET(self):
        try:
            parsed_path= urllib.parse.urlparse(self.path)
            if parsed_path.path == '/papers':
                out=[]
                for p in papers:
                    out.append({
                        "arxiv_id": p.get("arxiv_id"),
                        "title": p.get("title"),
                        "authors": p.get("authors", []),
                        "categories": p.get("categories",[])
                    })
                self.json_response(200, out)
                log_line(self.path, 200, f"returned {len(out)} papers") ## num res
                return
            elif parsed_path.path in paper_path:
                pid= urllib.parse.unquote(parsed_path.path.split("/papers/",1)[1])
                paper= idd.get(pid)
                if paper is not None:
                    self.json_response(200, paper)
                    log_line(self.path, 200)
                    return
                else:
                    self.json_response(404, self.err("Paper not found"))
                    log_line(self.path, 404)
                return
            elif parsed_path.path == '/search':
                qs= urllib.parse.parse_qs(parsed_path.query)
                q= (qs.get('q', [''])[0]or"").strip()
                if not q:
                    self.json_response(400, self.err("malformed search parameter"))
                    log_line(self.path, 400)
                    return
                terms= [t.lower() for t in re.findall(r"[A-Za-z0-9]+", q)]
                if not terms:
                    self.json_response(400, self.err("malformed search query"))
                    log_line(self.path, 400)
                    return
                results= []
                for p in (papers or []):
                    t= (p.get("title","") or "").lower()
                    a= (p.get("abstract","") or "").lower()
                    if not all(term in t or term in a for term in terms):
                        continue
                    s_title= sum(t.count(w) for w in terms)
                    s_abs= sum(a.count(w) for w in terms)
                    score= s_title + s_abs
                    if score > 0:
                        w= ([] if s_title==0 else ["title"]) + ([] if s_abs==0 else ["abstract"])
                        results.append({
                            "arxiv_id": p.get("arxiv_id"),
                            "title": p.get("title"),
                            "match_score": int(score),
                            "matches_in": w
                        })

                results.sort(key=lambda r: (-r["match_score"], r.get("title") or ""))
                payload= {"query": q, "results": results}
                self.json_response(200, payload)
                log_line(self.path, 200, f"{len(payload['results'])} results")
                return
            elif parsed_path.path == '/stats':
                self.json_response(200, corpuses)
                log_line(self.path, 200)
                return
            self.json_response(404, self.err("Not found"))
            log_line(self.path, 404)
            return
        except Exception as e:
            self.json_response(500, self.err(f"{type(e).__name__}: {e}"))
            log_line(self.path, 500)
            return
try:
    port= int(sys.argv[1]) if len(sys.argv) >= 2 else 8080
except ValueError:
    print(f"Invalid port number")
    sys.exit(1)
httpd= server_class(('', port), ArxivHandler)
print(f"Starting arXiv server on port {port}...", flush=True)
httpd.serve_forever()