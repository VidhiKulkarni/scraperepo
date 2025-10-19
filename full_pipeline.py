import os
import csv
import json
import re
import shutil
import subprocess
import time
import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

# --- Configuration ---
WORKSHOP_URL = "https://openreview.net/group?id=neurips.cc/2024/workshop/safegenai#tab-accept-oral"
FINAL_REPORT_CSV = "consolidated_secrets_report.csv"
TEMP_CLONE_DIR = "temp_pipeline_clones"
GITLEAKS_CONFIG_FILE = "generated_gitleaks_config.toml"
REQUEST_DELAY = 1  # Seconds to wait between network requests to be polite

# This TOML content will be written to the Gitleaks config file automatically.
GITLEAKS_CUSTOM_RULES_CONTENT = """
# Gitleaks custom configuration file for LLM API keys
# This file is generated automatically by the pipeline script.

[[rules]]
  id = "openai-api-key"
  description = "OpenAI API Key detected"
  regex = '''sk-(proj-)?([a-zA-Z0-9]{20,70})'''
  tags = ["api", "key", "llm", "openai"]
  keywords = ["openai", "sk-"]

[[rules]]
  id = "anthropic-api-key"
  description = "Anthropic API Key detected"
  regex = '''sk-ant-api\\d{2}-[\\w-]{95}'''
  tags = ["api", "key", "llm", "anthropic"]
  keywords = ["anthropic", "sk-ant-"]

[[rules]]
  id = "litellm-proxy-key"
  description = "LiteLLM Proxy Key detected"
  regex = '''sk-litellm-[a-zA-Z0-9]{24,64}'''
  tags = ["api", "key", "llm", "litellm"]
  keywords = ["litellm", "sk-litellm-"]

[[rules]]
  id = "fireworks-ai-api-key"
  description = "Fireworks AI API Key detected"
  regex = '''fw-[a-zA-Z0-9]{48}'''
  tags = ["api", "key", "llm", "fireworksai"]
  keywords = ["fireworks", "fw-"]

[[rules]]
  id = "together-ai-api-key"
  description = "Together AI API Key detected"
  regex = '''[a-fA-F0-9]{64}'''
  keywords = ["TOGETHER_API_KEY", "together_api_key", "together-api-key"]
  tags = ["api", "key", "llm", "togetherai"]
"""

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# --- Utility and Prerequisite Functions ---

def check_prerequisites():
    """Checks if git and gitleaks are installed."""
    print("--- Checking Prerequisites ---")
    if not shutil.which("git"):
        print("FATAL: 'git' command not found. Please install Git and ensure it's in your PATH.")
        return False
    if not shutil.which("gitleaks"):
        print("FATAL: 'gitleaks' command not found. Please install Gitleaks and ensure it's in your PATH.")
        return False
    print("✅ Git and Gitleaks are found.")
    return True

def create_gitleaks_config():
    """Creates the gitleaks config file from the embedded string."""
    print(f"✅ Creating Gitleaks config file: {GITLEAKS_CONFIG_FILE}")
    with open(GITLEAKS_CONFIG_FILE, "w") as f:
        f.write(GITLEAKS_CUSTOM_RULES_CONTENT)

# --- Phase 1: Data Scraping Functions ---

def scrape_authors_from_workshop(url):
    print(f"\n--- Phase 1: Scraping Authors from OpenReview ---")
    print(f"Fetching: {url}")
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"FATAL: Error fetching workshop URL: {e}")
        return []

    soup = BeautifulSoup(response.content, 'html.parser')
    papers = soup.find_all('div', class_='note')
    unique_authors = {}
    for paper in papers:
        authors_div = paper.find('div', class_='note-authors')
        if not authors_div: continue
        for link in authors_div.find_all('a'):
            author_name = link.get_text(strip=True)
            profile_suffix = link.get('href')
            if "Anonymous" in author_name or not profile_suffix: continue
            profile_url = f"https://openreview.net{profile_suffix}"
            if profile_url not in unique_authors:
                unique_authors[profile_url] = {'name': author_name, 'profile_url': profile_url}
    
    print(f"Found {len(unique_authors)} unique authors.")
    return list(unique_authors.values())

def find_researcher_details(author_list):
    print("\n--- Phase 2: Finding Homepages and GitHub Profiles ---")
    enriched_authors = []
    for i, author in enumerate(author_list):
        print(f"\n({i+1}/{len(author_list)}) Processing: {author['name']}")
        
        # Find Homepage
        homepage_url = find_homepage(author)
        author['homepage'] = homepage_url
        
        # Find GitHub Profile
        github_profile = find_github_profile(author['name'], homepage_url)
        author['github_profile'] = github_profile

        # Find Repositories
        if github_profile != "Not Found":
            repos = get_repos_from_github_api(github_profile)
            author['repos'] = repos
        else:
            author['repos'] = "N/A"
        
        enriched_authors.append(author)
    return enriched_authors

def find_homepage(author):
    profile_url = author['profile_url']
    if profile_url:
        try:
            time.sleep(REQUEST_DELAY)
            response = requests.get(profile_url, headers=HEADERS)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                homepage_div = soup.find('div', id='homepage')
                if homepage_div and homepage_div.find('a'):
                    print(f"  -> Found homepage on OpenReview profile.")
                    return homepage_div.find('a').get('href')
        except requests.exceptions.RequestException as e:
            print(f"  [WARN] Could not fetch profile {profile_url}: {e}")
    
    print(f"  -> Searching for homepage for {author['name']}...")
    try:
        time.sleep(REQUEST_DELAY)
        query = f'"{author["name"]}" AI researcher homepage OR personal website'
        results = list(DDGS().text(query, max_results=1))
        return results[0]['href'] if results else "Not Found"
    except Exception as e:
        print(f"  [ERROR] DuckDuckGo search failed: {e}")
        return "Search Failed"

def find_github_profile(name, homepage):
    if homepage and homepage.startswith('http'):
        try:
            time.sleep(REQUEST_DELAY)
            response = requests.get(homepage, headers=HEADERS, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                for link in soup.find_all('a', href=True):
                    if 'github.com/' in link['href']:
                        match = re.search(r'(https://github\.com/[^/]+)', link['href'])
                        if match:
                            print(f"  -> Found GitHub profile on homepage.")
                            return match.group(1)
        except requests.exceptions.RequestException:
            pass # Fail silently and proceed to search
    
    print(f"  -> Searching for GitHub profile for {name}...")
    try:
        time.sleep(REQUEST_DELAY)
        query = f'"{name}" site:github.com'
        results = list(DDGS().text(query, max_results=1))
        if results and 'github.com' in results[0]['href']:
            match = re.search(r'(https://github\.com/[^/]+)', results[0]['href'])
            return match.group(1) if match else "Not Found"
    except Exception as e:
        print(f"  [ERROR] DuckDuckGo search failed: {e}")
    return "Not Found"

def get_repos_from_github_api(profile_url):
    match = re.search(r'github\.com/([^/]+)', profile_url)
    if not match: return "Invalid Profile URL"
    username = match.group(1)
    api_url = f"https://api.github.com/users/{username}/repos?per_page=100"
    print(f"  -> Fetching repos for '{username}' from GitHub API...")
    try:
        time.sleep(REQUEST_DELAY)
        response = requests.get(api_url, headers=HEADERS, timeout=10)
        if response.status_code == 403: return "API Rate Limit Exceeded"
        response.raise_for_status()
        repos = response.json()
        repo_names = [repo['name'] for repo in repos if not repo['fork']]
        return "; ".join(repo_names) if repo_names else "No Public Repos Found"
    except requests.exceptions.RequestException:
        return "API Request Failed"

# --- Phase 2: Scanning Functions ---

def scan_repositories(researchers):
    print("\n--- Phase 3: Cloning and Scanning Repositories ---")
    all_findings = []
    
    if os.path.exists(TEMP_CLONE_DIR):
        shutil.rmtree(TEMP_CLONE_DIR)
    os.makedirs(TEMP_CLONE_DIR)

    for researcher in researchers:
        org_name = researcher['github_profile'].split('/')[-1] if researcher['github_profile'] != "Not Found" else "Unknown"
        repos_str = researcher.get('repos', '')
        
        if not repos_str or repos_str in ["N/A", "No Public Repos Found", "API Rate Limit Exceeded"]:
            continue
        
        print(f"\nScanning repos for {researcher['name']} ({org_name})")
        repo_names = [r.strip() for r in repos_str.split(';')]

        for repo_name in repo_names:
            repo_url = f"https://github.com/{org_name}/{repo_name}"
            local_repo_path = os.path.join(TEMP_CLONE_DIR, repo_name)
            
            print(f"  Cloning {repo_url}...")
            clone_result = subprocess.run(["git", "clone", "--depth", "1", repo_url, local_repo_path], capture_output=True, text=True)
            if clone_result.returncode != 0:
                print(f"  [FAIL] Could not clone. Skipping.")
                continue

            print(f"  Scanning with Gitleaks...")
            report_file = os.path.join(TEMP_CLONE_DIR, "report.json")
            subprocess.run([
                "gitleaks", "detect",
                "--config", GITLEAKS_CONFIG_FILE,
                "--source", local_repo_path,
                "--report-path", report_file,
                "--report-format", "json",
                "--no-git"
            ], capture_output=True)

            if os.path.exists(report_file) and os.path.getsize(report_file) > 0:
                with open(report_file, 'r') as f:
                    findings = json.load(f)
                print(f"  [SUCCESS] Found {len(findings)} potential secrets in {repo_name}.")
                for finding in findings:
                    all_findings.append({
                        'Organization Name': org_name,
                        'Person Name': researcher['name'],
                        'API Key (Secret)': finding['Secret'],
                        'Associated File Location': f"https://github.com/{org_name}/{repo_name}/blob/{finding['Commit']}/{finding['File']}"
                    })
            
            shutil.rmtree(local_repo_path) # Clean up repo immediately

    return all_findings

# --- Main Orchestrator ---

def main():
    print("====== AI Researcher Security Pipeline Starting ======")
    print("This tool scrapes researcher data and scans their public code for secrets.")
    print("Please use responsibly and ethically.")
    
    if not check_prerequisites():
        return
    
    create_gitleaks_config()

    # Phase 1 & 2: Scrape and enrich data
    authors = scrape_authors_from_workshop(WORKSHOP_URL)
    if not authors:
        print("No authors found. Exiting.")
        return
    
    researchers_with_repos = find_researcher_details(authors)

    # Phase 3: Scan repositories
    final_findings = scan_repositories(researchers_with_repos)

    # Phase 4: Report and Clean up
    print("\n--- Phase 4: Generating Final Report and Cleaning Up ---")
    if final_findings:
        print(f"✅ Found a total of {len(final_findings)} potential secrets.")
        with open(FINAL_REPORT_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=['Organization Name', 'Person Name', 'API Key (Secret)', 'Associated File Location'])
            writer.writeheader()
            writer.writerows(final_findings)
        print(f"✅ Final report saved to: {FINAL_REPORT_CSV}")
    else:
        print("✅ No secrets were found across all scanned repositories.")

    # Cleanup
    if os.path.exists(TEMP_CLONE_DIR):
        shutil.rmtree(TEMP_CLONE_DIR)
    if os.path.exists(GITLEAKS_CONFIG_FILE):
        os.remove(GITLEAKS_CONFIG_FILE)
    print("✅ All temporary files and directories have been removed.")
    print("\n====== Pipeline Finished. ======")


if __name__ == "__main__":
    main()