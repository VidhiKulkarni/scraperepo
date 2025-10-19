import requests
from bs4 import BeautifulSoup
import csv
import time
from duckduckgo_search import DDGS

# --- Configuration ---
#WORKSHOP_URL = "https://openreview.net/group?id=neurips.cc/2024/workshop/safegenai#tab-accept-oral"
WORKSHOP_URL = "https://openreview.net/group?id=neurips.cc/2024/workshop/safegenai#tab-accept-oral"
OUTPUT_CSV_FILE = "researchers_safegenai_2024.csv"
REQUEST_DELAY = 1  # Seconds to wait between requests to be polite to the server

# --- Headers to mimic a browser ---
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

def get_authors_from_workshop(url):
    """
    Scrapes the main workshop page to get a list of papers and their authors.
    Returns a list of dictionaries, each containing author name, paper title, and profile URL.
    """
    print(f"Fetching workshop page: {url}")

    


    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()  # Raise an exception for bad status codes
    except requests.exceptions.RequestException as e:
        print(f"Error fetching workshop URL: {e}")
        return []
    
    match = response.search(r"group\?id=([A-Za-Z0-9./_-]+)", url)

    print(response)
    soup = BeautifulSoup(response.content, 'html.parser')
    papers = soup.find_all('div', class_='note')
    print(papers)
    
    author_list = []
    print(f"Found {len(papers)} papers on the page.")

    for paper in papers:
        # Get paper title
        title_tag = paper.find('h4', class_='note-title')
        paper_title = title_tag.get_text(strip=True) if title_tag else "Unknown Title"

        # Get authors
        authors_div = paper.find('div', class_='note-authors')
        if not authors_div:
            continue
        
        author_links = authors_div.find_all('a')
        for link in author_links:
            author_name = link.get_text(strip=True)
            # Filter out anonymous authors
            if "Anonymous" in author_name:
                continue
            
            profile_url_suffix = link.get('href')
            author_info = {
                'name': author_name,
                'paper_title': paper_title,
                'profile_url': f"https://openreview.net{profile_url_suffix}" if profile_url_suffix else None
            }
            author_list.append(author_info)
            
    return author_list

def find_homepage(author_info):
    """
    Tries to find the homepage for a single author.
    First checks their OpenReview profile, then falls back to a web search.
    """
    name = author_info['name']
    profile_url = author_info['profile_url']

    # Strategy 1: Check OpenReview Profile (High-Confidence)
    if profile_url:
        try:
            time.sleep(REQUEST_DELAY) # Polite delay
            response = requests.get(profile_url, headers=HEADERS)
            if response.status_code == 200:
                profile_soup = BeautifulSoup(response.content, 'html.parser')
                # OpenReview profiles often have a div with id 'homepage'
                homepage_div = profile_soup.find('div', id='homepage')
                if homepage_div and homepage_div.find('a'):
                    homepage_url = homepage_div.find('a').get('href')
                    print(f"  [SUCCESS] Found homepage for {name} on OpenReview profile.")
                    return homepage_url
        except requests.exceptions.RequestException as e:
            print(f"  [WARN] Could not fetch OpenReview profile for {name}: {e}")

    # Strategy 2: Fallback to DuckDuckGo Search (Best-Guess)
    print(f"  [INFO] Searching DuckDuckGo for {name}'s homepage...")
    try:
        time.sleep(REQUEST_DELAY) # Polite delay
        query = f'"{name}" AI researcher homepage OR personal website'
        search_results = list(DDGS().text(query, max_results=1))
        
        if search_results:
            homepage_guess = search_results[0]['href']
            print(f"  [GUESS] Found potential homepage for {name} via search.")
            return homepage_guess
        else:
            print(f"  [FAIL] No homepage found for {name} via search.")
            return "Not Found"
    except Exception as e:
        print(f"  [ERROR] DuckDuckGo search failed for {name}: {e}")
        return "Search Failed"


def save_to_csv(data, filename):
    """Saves the final data to a CSV file."""
    if not data:
        print("No data to save.")
        return

    with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['Author Name', 'Paper Title', 'Homepage (Best Guess)']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

        writer.writeheader()
        for item in data:
            writer.writerow({
                'Author Name': item['name'],
                'Paper Title': item['paper_title'],
                'Homepage (Best Guess)': item['homepage']
            })
    print(f"\nSuccessfully saved data for {len(data)} researchers to {filename}")


if __name__ == "__main__":
    # Step 1: Get all authors from the workshop page
    authors = get_authors_from_workshop(WORKSHOP_URL)
    
    if not authors:
        print("Could not find any authors. Exiting.")
    else:
        # Use a dictionary to avoid processing the same author multiple times
        unique_authors = {author['profile_url']: author for author in authors if author['profile_url']}
        print(f"\nFound {len(authors)} author mentions, corresponding to {len(unique_authors)} unique profiles.")
        
        final_results = []
        
        # Step 2: Find homepage for each unique author
        for i, author_info in enumerate(unique_authors.values()):
            print(f"\nProcessing author {i+1}/{len(unique_authors)}: {author_info['name']}")
            homepage = find_homepage(author_info)
            
            # Add the found homepage to our data
            result = author_info.copy()
            result['homepage'] = homepage
            final_results.append(result)

        # Step 3: Save all collected data to a CSV
        save_to_csv(final_results, OUTPUT_CSV_FILE)

