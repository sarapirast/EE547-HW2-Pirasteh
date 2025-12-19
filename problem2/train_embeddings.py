import sys,os,json,re,datetime
from collections import Counter
import torch
import torch.nn as nn
import torch.optim as optim



def time_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def clean_text(text):
    text= text.lower() # Convert to lowercase
    text= re.sub(r'[^a-z\s]', ' ', text)# Remove non-alphabetic characters except spaces
    words=text.split()# Split into words
    words=[word for word in words if len(word)>=2]# Remove very short words (< 2 characters)
    return words

def build_vocabulary(texts, top=5000):
    word_counter= Counter()
    total_words= 0
    for tok in texts:
        words= clean_text(tok)
        word_counter.update(words)
        total_words+= len(words)
    most_common= word_counter.most_common(top)
    vocab= {"<UNK>": 0}
    for idx, (word, _) in enumerate(most_common, start=1):
        vocab[word]= idx
    idx_vocab= {str(idx): word for word, idx in vocab.items()}
    return vocab, idx_vocab, total_words

def seq_encode(texts, vocab, max_len=100):
    sequences= []
    for tok in texts:
        words= clean_text(tok)
        seq= [vocab.get(word, 0) for word in words][:max_len]
        seq+= [0]*(max_len - len(seq))  # Padding
        sequences.append(seq)
    return torch.tensor(sequences, dtype=torch.long)



##need to convert vocab and feed to encoder
def convert_to_bow(sequences, vocab_size):
    b=[]
    for i in sequences:
        v=torch.zeros(vocab_size,dtype=torch.float32)
        for idx in i:
            if idx!=0: #ignore padding
                v[idx]= 1.0
        b.append(v)
    return b #binary


class TextAutoencoder(nn.Module):
    def __init__(self, vocab_size, hidden_dim, embedding_dim):
        super().__init__()
        # Encoder: vocab_size → hidden_dim → embedding_dim
        self.encoder = nn.Sequential(
            nn.Linear(vocab_size, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, embedding_dim)
        )
        
        # Decoder: embedding_dim → hidden_dim → vocab_size  
        self.decoder = nn.Sequential(
            nn.Linear(embedding_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, vocab_size),
            nn.Sigmoid()  # Output probabilities
        )
    
    def forward(self, x):
        # Encode to bottleneck
        embedding = self.encoder(x)
        # Decode back to vocabulary space
        reconstruction = self.decoder(embedding)
        return reconstruction, embedding


def params_count(model):
    return sum(p.numel() for p in model.parameters())

def train_autoencoder(sequences, vocab_size, hidden_dim=256, embedding_dim=64, epochs=10, batch_size=32, lr=0.001):
    model= TextAutoencoder(vocab_size, hidden_dim, embedding_dim)
    criterion= nn.BCELoss()
    optimizer= optim.Adam(model.parameters(), lr=lr)
    

    total_params= params_count(model)
    print("Total parameters:",total_params)
    if total_params>2000000:
        print("ERROR: parameter limit exceeded")
        sys.exit(1)
    bows= convert_to_bow(sequences.tolist(), vocab_size)
    sequences= torch.stack(bows)
    dataset= torch.utils.data.TensorDataset(sequences)
    dataloader= torch.utils.data.DataLoader(dataset, batch_size=batch_size, shuffle=True)
    
    for epoch in range(epochs):
        total_loss= 0.0
        for batch in dataloader:
            inputs= batch[0].float()
            optimizer.zero_grad()
            outputs, _= model(inputs)
            loss= criterion(outputs, inputs)
            loss.backward()
            optimizer.step()
            total_loss+= loss.item()*inputs.size(0)
        
        avg_loss= total_loss/len(dataset)
        print(f"Epoch [{epoch+1}/{epochs}], Loss: {avg_loss:.4f}")
    finalloss= avg_loss
    return model,finalloss

def save_outputs(model,bows,ids,vocab,idx_vocab,total_words,vocab_size,hidden_dim,embedding_dim,output_dir):
    os.makedirs(output_dir, exist_ok=True)
    criterion= nn.BCELoss()
    model.eval()
    emb= []
    with torch.no_grad():
        for arxiv_id,x in zip(ids,bows):
            x1= x.unsqueeze(0).float()
            r, embedding= model(x1)
            rloss= criterion(r, x1).item()
            emb.append({
                "arxiv_id": arxiv_id,
                "embedding": embedding.squeeze(0).tolist(),
                "reconstruction_loss": float(rloss)
            })

    torch.save({
        "model_state_dict": model.state_dict(),
        "vocab_to_idx": vocab,
        "model_config": {
            "vocab_size": vocab_size,
            "hidden_dim": hidden_dim,
            "embedding_dim": embedding_dim
        }
    }, os.path.join(output_dir, "model.pth"))

    with open(os.path.join(output_dir, "embeddings.json"), 'w', encoding='utf-8') as f:
        json.dump(emb, f,indent=2)

    with open(os.path.join(output_dir, "vocabulary.json"), 'w', encoding='utf-8') as f:
        json.dump({
            "vocab_to_idx": vocab,
            "idx_to_vocab": idx_vocab,
            "vocab_size": vocab_size,
            "total_words": total_words
        }, f, indent=2)

if len(sys.argv)<3:
    print("<input_json> <output_dir> [--epochs 50] [--batch_size 32]")
    sys.exit(1)
input_json= sys.argv[1]
output_dir= sys.argv[2]
epochs= 50
batch_size= 32
hidden_dim= 256
embedding_dim= 64
max_len= 100

if '--epochs' in sys.argv:
    epochs= int(sys.argv[sys.argv.index('--epochs')+1])
if '--batch_size' in sys.argv:
    batch_size= int(sys.argv[sys.argv.index('--batch_size')+1])


start=time_now()
with open(input_json, 'r', encoding='utf-8') as f:
    papers= json.load(f)
ids=[]
texts=[]
for paper in papers:
    arxiv_id= paper.get('arxiv_id')
    abstract= paper.get('abstract', '')
    if isinstance(arxiv_id, str) and isinstance(abstract, str) and abstract.strip():
        ids.append(arxiv_id)
        texts.append(abstract)
vocab, idx_vocab, total_words= build_vocabulary(texts)
vocab_size= len(vocab)
sequences= seq_encode(texts, vocab, max_len=max_len)
model,finalloss= train_autoencoder(sequences, vocab_size, hidden_dim, embedding_dim, epochs, batch_size)
total_params= params_count(model)
bows= convert_to_bow(sequences.tolist(), vocab_size)
save_outputs(model, bows, ids, vocab, idx_vocab,total_words, vocab_size, hidden_dim, embedding_dim, output_dir)
end=time_now()
os.makedirs(output_dir, exist_ok=True)
with open(os.path.join(output_dir, "training_log.json"), 'w', encoding='utf-8') as f:
    json.dump({
        "start_time": start,
        "end_time": end,
        "epochs": epochs,
        "final_loss": float(finalloss),
        "total_parameters": total_params,
        "papers_processed": len(texts),
        "embedding_dim": embedding_dim
    }, f, indent=2) 