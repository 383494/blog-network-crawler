import csv
import json
from urllib.parse import urlparse
import os

# --- Configuration ---
INPUT_CSV_FILE = "friend_graph.csv"
STATE_FILE = "crawler_state.json"
NO_OUT_EDGES_FILE = "no_out_edges_sites.txt"
MANUAL_CHECK_FILE = "manual_intervention_required.txt"
OUTPUT_JSON_FILE = "graph_data.json"

def get_domain(url: str) -> str:
    """Extracts the network location (domain) from a URL."""
    try:
        if not url: return ""
        if '://' not in url:
            url = 'http://' + url
        return urlparse(url).netloc.replace('www.', '')
    except Exception:
        return ""

def load_txt_list(filepath):
    """Helper to load URLs from the fallback txt files."""
    urls = []
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            # Skip the first descriptive line
            lines = f.readlines()[1:]
            urls = [line.strip() for line in lines if line.strip()]
    return urls

def process_graph():
    if not os.path.exists(INPUT_CSV_FILE):
        print(f"Error: {INPUT_CSV_FILE} not found.")
        return

    # 1. Load Crawler State
    visited_domains = set()
    queue_domains = set()
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
            # Crawler state saves visited_domains as a list
            visited_domains = set(state.get("visited_domains", []))
            queue_domains = {get_domain(u) for u in state.get("queue", []) if get_domain(u) != "" and get_domain(u) not in visited_domains}

    # 2. Load Failure/Manual Lists
    no_out_domains = {get_domain(u) for u in load_txt_list(NO_OUT_EDGES_FILE) if get_domain(u)}
    manual_domains = {get_domain(u) for u in load_txt_list(MANUAL_CHECK_FILE) if get_domain(u)}

    # 3. Process CSV for Edges and initial Node Map
    domain_to_id = {}
    id_to_domain = []
    edges = list()
    next_id = 0

    def add_domain(domain):
        nonlocal next_id
        if domain and domain not in domain_to_id:
            domain_to_id[domain] = next_id
            id_to_domain.append(domain)
            next_id += 1
        return domain_to_id.get(domain)

    with open(INPUT_CSV_FILE, mode='r', newline='', encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader, None) # Skip header
        for row in reader:
            if len(row) < 2: continue
            src_dom = get_domain(row[0])
            tgt_dom = get_domain(row[1])
            if src_dom and tgt_dom and src_dom != tgt_dom:
                s_id = add_domain(src_dom)
                t_id = add_domain(tgt_dom)
                edges.append((s_id, t_id))

    # 4. Ensure all Border/Possible Border domains are in the ID map
    all_frontier = queue_domains | no_out_domains | manual_domains
    for dom in all_frontier:
        add_domain(dom)

    # 5. Categorize IDs
    # border_ids: In queue but NOT visited
    border_ids = sorted([domain_to_id[d] for d in queue_domains if d not in visited_domains])
    
    # possible_border_ids: No outer edges found OR manual captcha required
    possible_border_ids = sorted(list({domain_to_id[d] for d in (no_out_domains | manual_domains)}))

    # 6. Build Adjacency List
    #adjacency_list = {str(i): [] for i in range(len(id_to_domain))}
    #for s, t in edges:
    #    adjacency_list[str(s)].append(t)

    # 7. Construct Final JSON Structure
    output = {
        "metadata": {
            "source_file": INPUT_CSV_FILE,
            "node_mapping": {
                "comment": "Maps node ID (integer) to domain (string). The list index corresponds to the ID.",
                "domains": id_to_domain
            }
        },
        "node_count": len(id_to_domain),
        "edge_count": len(edges),
        "border_ids": border_ids,
        "possible_border_ids": possible_border_ids,
        "graph_representations": {
            "edge_list": {
                "comment": "List of [source_id, target_id] pairs representing directed edges.",
                "edges": [list(e) for e in edges]
            }
        }
    }

    with open(OUTPUT_JSON_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=4)
    
    print(f"[*] Graph processed: {len(id_to_domain)} nodes, {len(edges)} edges.")
    print(f"[*] {len(border_ids)} border nodes, {len(possible_border_ids)} possible border nodes.")
    print(f"[*] Output saved to {OUTPUT_JSON_FILE}")

if __name__ == "__main__":
    process_graph()
