import csv
import networkx as nx
from pyvis.network import Network
from urllib.parse import urlparse
import re  # Only addition needed for static post-stabilization fix

CSV_FILENAME = "friend_graph.csv"
OUTPUT_HTML = "blog_network.html"

def get_domain(url):
    try:
        return urlparse(url).netloc.replace('www.', '')
    except:
        return url

def extract_visited_pages():
    try:
        ans = set()
        with open(CSV_FILENAME, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                source = row["Source_Friend_Page"]
                target = row["Target_Blog"]
              
                source_domain = get_domain(source)
                target_domain = get_domain(target)
              
                if not source_domain or not target_domain:
                    continue
                ans.add(source_domain)
        print(list(ans))
    except FileNotFoundError:
        print("CSV file not found. Please run the crawler first.")
        return

def visualize():
    # Initialize NetworkX directed graph
    G = nx.DiGraph()
   
    # Read the CSV (same logic as before)
    print(f"Reading data from {CSV_FILENAME}...")
    try:
        with open(CSV_FILENAME, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                source = row["Source_Friend_Page"]
                target = row["Target_Blog"]
              
                source_domain = get_domain(source)
                target_domain = get_domain(target)
              
                if not source_domain or not target_domain:
                    continue
               
                # Add nodes with titles (hover tooltips) — exact same as original
                G.add_node(source_domain, title=f"Link Page: {source}")
                G.add_node(target_domain, title=f"Blog URL: {target}")
               
                # Add directed edge
                G.add_edge(source_domain, target_domain)
    except FileNotFoundError:
        print("CSV file not found. Please run the crawler first.")
        return
    print(f"Graph created with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")

    # Initialize PyVis network — EXACT same config as original (BarnesHut, stabilization, interaction, etc.)
    net = Network(height="800px", width="100%", bgcolor="#222222", font_color="white", directed=True)
  
    net.barnes_hut()
    net.set_options('''
    {
      "physics": {
        "barnesHut": {
          "gravitationalConstant": -80000,
          "centralGravity": 0.3,
          "springLength": 250,
          "springConstant": 0.001,
          "damping": 0.09
        },
        "maxVelocity": 200,
        "minVelocity": 0.75,
        "timestep": 0.5,
        "stabilization": {
          "enabled": true,
          "iterations": 1500,
          "updateInterval": 10
        }
      },
      "interaction": {
        "hideEdgesOnDrag": false,
        "hideNodesOnDrag": false
      }
    }
    ''')

    for node, data in G.nodes(data=True):
        net.add_node(
            node,
            label=node,
            title=data.get("title", ""),
            size=10,
            shape="dot"
        )
    for source, target in G.edges():
        net.add_edge(source, target)

    # Save the HTML (still dynamic during stabilization)
    net.show(OUTPUT_HTML, notebook=False)

    # This makes every node permanently fixed once the simulation finishes.
    # No change to BarnesHut parameters, node sizes, colors, edges, or visual layout.
    print("Applying static mode (nodes will be fixed after stabilization)...")
    with open(OUTPUT_HTML, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Insert the disable command inside PyVis's built-in stabilization callback
    # (This runs in the browser exactly when the 1500 iterations complete)
    pattern = r'(network\.once\("stabilizationIterationsDone", function\(\) \{)'
    replacement = r'\1\n                    // STATIC VERSION: Disable physics - all points now permanently fixed\n                    network.setOptions({ physics: { enabled: false } });'
    html_content = re.sub(pattern, replacement, html_content)

    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"Visualization saved to {OUTPUT_HTML}. Open this file in your browser!")
    print("→ Graph is now STATIC: exact same layout as before, but every node is fixed when stable.")

if __name__ == "__main__":
    visualize()
