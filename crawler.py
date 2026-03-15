import asyncio
import json
import csv
import os
import sys
import re
import random
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from openai import AsyncOpenAI

CONFIG_FILE = "config.json"

# Ignore these common domains to save LLM tokens and avoid false positives
BLACKLIST_DOMAINS =list(set([
    'twitter.com',
    'facebook.com',
    'youtube.com',
    'instagram.com',
    'google.com',
    "github.com",
    "x.com",
]))

def load_config():
    """Loads configuration or creates a default one if it doesn't exist."""
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "api_key": "YOUR_API_KEY_HERE",
            "api_base_url": "https://api.openai.com/v1",
            "model_name": "gpt-4o-mini",
            "llm_timeout_seconds": 30,
            "proxy_server": "",
            "start_url": "https://yoursite.com",
            "max_blogs_to_visit": 50,
            "max_clicks_per_blog": 3,
            "csv_filename": "friend_graph.csv",
            "state_filename": "crawler_state.json"
        }
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, indent=4)
        print(f"[*] Created default {CONFIG_FILE}.")
        print("[*] Please edit it with your API key, starting URL, then run again.")
        sys.exit(0)

    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_state(state_file, start_url):
    """Loads the crawler state to resume progress."""
    if os.path.exists(state_file):
        with open(state_file, 'r', encoding='utf-8') as f:
            state = json.load(f)
            state['visited_domains'] = set(state.get('visited_domains', []))
            state['manual_queue'] = state.get('manual_queue',[])
            state['no_out_edges_sites'] = state.get('no_out_edges_sites',[])
            print(f"[*] Resuming: {state['blogs_visited']} visited, {len(state['queue'])} in queue.")
            return state

    print("[*] Starting fresh crawl.")
    return {
        "queue":[start_url],
        "manual_queue":[],
        "no_out_edges_sites":[],
        "visited_domains": set(),
        "blogs_visited": 0
    }

def save_state(state_file, queue, manual_queue, no_out_edges_sites, visited_domains, blogs_visited):
    """Checkpoints the current progress to a file."""
    state = {
        "queue": queue,
        "manual_queue": manual_queue,
        "no_out_edges_sites": no_out_edges_sites,
        "visited_domains": list(visited_domains),
        "blogs_visited": blogs_visited
    }
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=4)

    if manual_queue:
        with open("manual_intervention_required.txt", "w", encoding='utf-8') as txt:
            txt.write("These URLs require manual verification (CAPTCHA/Cloudflare):\n")
            for url in manual_queue: txt.write(url + "\n")
    if no_out_edges_sites:
        with open("no_out_edges_sites.txt", "w", encoding='utf-8') as txt:
            txt.write("These URLs were explored but no friend links were found:\n")
            for url in no_out_edges_sites: txt.write(url + "\n")

def get_domain(url):
    try:
        return urlparse(url).netloc.replace('www.', '')
    except:
        return ""

def is_blacklisted(url):
    domain = get_domain(url)
    return any(b in domain for b in BLACKLIST_DOMAINS)

async def is_captcha_page(page, title):
    title_lower = title.lower() if title else ""
    captcha_titles =["just a moment...", "attention required", "security check", "cloudflare", "robot check", "verify you are human"]
    if any(c in title_lower for c in captcha_titles): return True
    try:
        captcha_elements = await page.locator('#cf-challenge-running, #cf-please-wait, .cf-turnstile, iframe[src*="recaptcha"], iframe[src*="hcaptcha"]').count()
        if captcha_elements > 0: return True
    except Exception: pass
    return False

async def extract_links(page):
    links = await page.evaluate('''() => { return Array.from(document.querySelectorAll('a')).map(a => ({ text: a.innerText.trim(), href: a.href })).filter(a => a.href.startsWith('http') && a.text.length > 0); }''')
    clean_links =[l for l in links if not is_blacklisted(l['href'])]
    return clean_links[:100]

async def ask_llm_for_action(client, model_name, url, title, links, timeout):
    prompt = f"""
    Determine if the Current Page IS the "friend links" page of a blog.
    - If it IS, extract all the URLs of the friends' blogs.
    - If it IS NOT, find the BEST link that leads to the friend links page (e.g., "Links", "Friends", "Blogroll").
    - If no candidate link and not a friend page, set give_up to true.
    Return JSON: {{"is_friend_page": bool, "friend_links": [], "next_probable_page": str or null, "give_up": bool}}

    Current Page: {url}
    Title: {title}
    Links: {json.dumps(links, ensure_ascii=False)}
    """
    print(f"  ->[LLM] Analyzing {len(links)} links... (timeout: {timeout}s)")
    try:
        api_call = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        response = await asyncio.wait_for(api_call, timeout=timeout)
        return json.loads(response.choices[0].message.content)
    except asyncio.TimeoutError:
        print(f"  -> [LLM Error]: Timeout.")
        return {"timeout": True}
    except Exception as e:
        print(f"  ->[LLM Error]: {e}")
        return {"is_friend_page": False, "friend_links":[], "next_probable_page": None, "give_up": True}

async def fallback_curl_extract(client, model_name, url, proxy, timeout):
    print(f"  -> [Curl Fallback] Fetching raw HTML for {url}...")
    args =["curl", "-sL", "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FriendLinkCrawler/1.0"]
    if proxy:
        args.extend(["-x", proxy])
    args.append(url)

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=20.0)
        html_content = stdout.decode('utf-8', errors='ignore')

        links_data =[]
        seen_urls = set()

        for match in re.finditer(r'<a([^>]*)>(.*?)</a>', html_content, re.IGNORECASE | re.DOTALL):
            attrs = match.group(1)
            inner_html = match.group(2)

            href_match = re.search(r'href=["\'](https?://[^"\']+)["\']', attrs, re.IGNORECASE)
            if href_match:
                found_url = href_match.group(1)
                if found_url in seen_urls or is_blacklisted(found_url):
                    continue
                seen_urls.add(found_url)

                text = re.sub(r'<[^>]+>', ' ', inner_html)
                title_match = re.search(r'title=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
                title = title_match.group(1) if title_match else ""
                context = f"{title} {text}".strip()
                context = re.sub(r'\s+', ' ', context)

                links_data.append({
                    "url": found_url,
                    "text": context[:150] if context else "No visible text"
                })

        raw_links = re.findall(r'https?://[^\s"\'<>]+', html_content)
        for found_url in raw_links:
            if found_url not in seen_urls and not is_blacklisted(found_url):
                seen_urls.add(found_url)
                links_data.append({"url": found_url, "text": "Raw URL (no context)"})

        if not links_data:
            return[]

        prompt = f"""
        I extracted the following links along with their inner text/context from the HTML source of {url} using curl.
        Identify which of these URLs belong to friends' blogs (friend links / blogroll).
        Return ONLY a JSON object with a single key "friend_links" containing a list of strings (the URLs). If none, return an empty list.

        Links Data:
        {json.dumps(links_data[:150], ensure_ascii=False)}
        """

        print(f"  -> [LLM Fallback] Analyzing {min(len(links_data), 150)} links with context... (timeout: {timeout}s)")
        api_call = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        response = await asyncio.wait_for(api_call, timeout=timeout)
        result = json.loads(response.choices[0].message.content)
        return result.get("friend_links",[])

    except asyncio.TimeoutError:
        print("  ->[Curl/LLM Fallback Error]: Timeout.")
        return[]
    except Exception as e:
        print(f"  -> [Curl/LLM Fallback Error]: {e}")
        return[]

async def crawl():
    config = load_config()
    client_kwargs = {"api_key": config["api_key"]}
    if config.get("api_base_url"):
        client_kwargs["base_url"] = config["api_base_url"]
    client = AsyncOpenAI(**client_kwargs)

    state_file = config["state_filename"]
    state = load_state(state_file, config["start_url"])
    queue = state["queue"]
    manual_queue = state["manual_queue"]
    no_out_edges_sites = state["no_out_edges_sites"]
    visited_domains = state["visited_domains"]
    blogs_visited = state["blogs_visited"]

    csv_file = config["csv_filename"]
    if not os.path.exists(csv_file):
        with open(csv_file, mode='w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow(["Source_Friend_Page", "Target_Blog"])

    async with async_playwright() as p:
        launch_args = {"headless": True}
        if config.get("proxy_server"):
            launch_args["proxy"] = {"server": config["proxy_server"]}
            print(f"[*] Using Proxy: {config['proxy_server']}")

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) FriendLinkCrawler/1.0")

        try:
            while queue and blogs_visited < config["max_blogs_to_visit"]:
                page = await context.new_page()

                try:
                    current_base_url = queue.pop(random.randrange(0, len(queue)))
                    domain = get_domain(current_base_url)

                    if not domain or domain in visited_domains:
                        await page.close()
                        continue
                    visited_domains.add(domain)

                    print(f"\n[{blogs_visited+1}/{config['max_blogs_to_visit']}] Blog: {current_base_url}")
                    current_url, depth, found_friends, is_manual = current_base_url, 0, False, False

                    while depth < config["max_clicks_per_blog"]:
                        print(f"  -> Navigating: {current_url} (D{depth})")
                        try:
                            await page.goto(current_url, timeout=25000, wait_until="load")
                            await page.wait_for_timeout(1500)
                            await page.wait_for_load_state("domcontentloaded")
                            title = await page.title()

                            if await is_captcha_page(page, title):
                                print(f"  ->[CAPTCHA]: {current_url}")
                                if current_url not in manual_queue: manual_queue.append(current_url)
                                is_manual = True
                                break

                            links = await extract_links(page)
                        except Exception as e:
                            print(f"  -> Page Load Error: {e}")
                            break

                        llm_decision = await ask_llm_for_action(client, config["model_name"], current_url, title, links, config["llm_timeout_seconds"])

                        if llm_decision.get("timeout"):
                            break

                        if llm_decision.get("is_friend_page"):
                            friends_raw = llm_decision.get("friend_links", [])
                            friends = []
                            if isinstance(friends_raw, list):
                                for item in friends_raw:
                                    if isinstance(item, str) and item.startswith('http'):
                                        friends.append(item)
                                    elif isinstance(item, dict):
                                        # Check for common keys 'href' or 'url'
                                        url = item.get("href") or item.get("url")
                                        if isinstance(url, str) and url.startswith('http'):
                                            friends.append(url)
                            
                            if friends:
                                print(f"  -> Found {len(friends)} friend links.")
                                with open(csv_file, mode='a', newline='', encoding='utf-8') as f:
                                    writer = csv.writer(f)
                                    for friend_url in friends:
                                        writer.writerow([current_url, friend_url])
                                        queue.append(friend_url)
                                found_friends = True
                            else:
                                print("  -> LLM marked as friend page, but no links were extracted.")

                            break # Stop crawling this site after finding the friend page
                        elif llm_decision.get("next_probable_page") and not llm_decision.get("give_up"):
                            current_url = llm_decision["next_probable_page"]
                            depth += 1
                        else:
                            break

                    if not found_friends and not is_manual:
                        curl_friends_raw = await fallback_curl_extract(client, config["model_name"], current_base_url, config.get("proxy_server"), config["llm_timeout_seconds"])

                        if curl_friends_raw:
                            curl_friends = []
                            if isinstance(curl_friends_raw, list):
                                for item in curl_friends_raw:
                                    if isinstance(item, str) and item.startswith('http'):
                                        curl_friends.append(item)
                                    elif isinstance(item, dict):
                                        url = item.get("href") or item.get("url")
                                        if isinstance(url, str) and url.startswith('http'):
                                            curl_friends.append(url)

                            if curl_friends:
                                print(f"  -> Found {len(curl_friends)} links via curl fallback.")
                                with open(csv_file, mode='a', newline='', encoding='utf-8') as f:
                                    writer = csv.writer(f)
                                    for friend_url in curl_friends:
                                        writer.writerow([current_base_url, friend_url])
                                        queue.append(friend_url)
                                found_friends = True

                        if not found_friends:
                            if current_base_url not in no_out_edges_sites: no_out_edges_sites.append(current_base_url)

                    blogs_visited += 1
                    save_state(state_file, queue, manual_queue, no_out_edges_sites, visited_domains, blogs_visited)

                finally:
                    if not page.is_closed():
                        await page.close()

        except KeyboardInterrupt:
            print("\n[*] Interrupted. Progress saved.")
        finally:
            await browser.close()

if __name__ == "__main__":
    try:
        asyncio.run(crawl())
    except KeyboardInterrupt:
        pass
