import json
import requests
import xml.etree.ElementTree as ET
import os
import time
import random
from typing import List, Tuple
from dotenv import load_dotenv

# --- Configuration & Setup ---
NCBI_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent"
HEADERS = {'Content-Type': 'application/json'}

# Set the maximum number of articles to retrieve for the single search query
MAX_ARTICLES = 100

# API Keys
load_dotenv()
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Check if keys are available for a clean startup
if not NCBI_API_KEY or not GEMINI_API_KEY:
    print("FATAL ERROR: API keys not found. Please ensure you have set them in your environment or a local .env file.")
    # Exit gracefully if keys are missing
    exit()

def print_compliance_notice():
    print("-" * 60)
    print("NCBI COMPLIANCE NOTICE")
    print("This tool uses NCBI E-utilities. By proceeding, you agree to")
    print("NCBI's Disclaimer and Copyright notice:")
    print("https://www.ncbi.nlm.nih.gov/About/disclaimer.html")
    print("-" * 60)
    print()

# --- Helper Function for Rate Limiting ---
def exponential_backoff_request(url: str, method: str = "GET", payload: dict = None, max_retries: int = 5) -> (
        requests.Response | None):
    """
    Makes an HTTP request with exponential backoff for handling rate limits (429) and server errors.
    """
    for attempt in range(max_retries):
        try:
            if method == "GET":
                response = requests.get(url)
            else:
                response = requests.post(url, headers=HEADERS, json=payload)

            # Success or non-rate-limit client error (4xx other than 429)
            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                return response

            # Rate Limit (429) or Server Error (5xx) detected
            if response.status_code == 429:
                print(f"RATE LIMIT HIT (429) on attempt {attempt + 1}. Retrying...")
            else:  # 5xx errors
                print(f"Server Error ({response.status_code}) on attempt {attempt + 1}. Retrying...")

            # Calculate wait time: base_delay * 2^attempt + jitter
            delay = (2 ** attempt) + random.uniform(0, 1)
            print(f"Waiting for {delay:.2f} seconds before retry.")
            time.sleep(delay)

        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}. Retrying...")
            delay = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)

    print(f"Failed to retrieve data after {max_retries} attempts.")
    return None


def fetch_abstracts(search_term: str) -> Tuple[str, List[str]]:
    """
    Executes ESearch and EFetch to retrieve abstracts for the top MAX_ARTICLES results.
    Returns a single concatenated abstract string and a list of PMIDs.
    """
    api_key_param = f"&api_key={NCBI_API_KEY}"

    print(f"\n[Processing {search_term}] --- Step 1: ESearch (Get {MAX_ARTICLES} IDs) ---")

    # ESearch URL: Increased retmax to MAX_ARTICLES and sort by relevance
    esearch_url = (
        f"{NCBI_BASE_URL}esearch.fcgi?db=pubmed&term={search_term.replace(' ', '+')}"
        f"&retmode=json&retmax={MAX_ARTICLES}&sort=relevance{api_key_param}"
    )

    response = exponential_backoff_request(esearch_url)
    if not response:
        return f"Error: ESearch failed after retries for '{search_term}'.", []

    search_data = response.json()
    pmids = search_data['esearchresult']['idlist']

    if not pmids:
        return f"Error: No results found for the query '{search_term}'.", []

    pmid_list_str = ",".join(pmids)
    print(f"Found {len(pmids)} PMIDs: {pmid_list_str}")

    print("--- Step 2: EFetch (Get All Abstracts) ---")
    # EFetch URL: Passes the list of PMIDs for a single batch request
    efetch_url = (
        f"{NCBI_BASE_URL}efetch.fcgi?db=pubmed&id={pmid_list_str}&retmode=xml&rettype=abstract{api_key_param}"
    )

    response = exponential_backoff_request(efetch_url)
    if not response:
        return f"Error: EFetch failed after retries for {pmids}.", pmids

    # EFetch returns XML containing multiple articles
    root = ET.fromstring(response.text)
    combined_abstract_text = ""

    # The XML structure has multiple PubmedArticle elements
    for article in root.findall('.//PubmedArticle'):
        # Extract PMID
        pmid_el = article.find(".//PMID")
        article_pmid = pmid_el.text if pmid_el is not None else "UnknownPMID"

        # Extract Title
        title_el = article.find(".//ArticleTitle")
        title = title_el.text if title_el is not None else "No Title"

        # Extract First Author for context
        author_el = article.find(".//Author/LastName")
        author = author_el.text if author_el is not None else "Unknown"

        abstract_text = ""
        # Look for AbstractText within the current article
        abstract_elements = article.findall('.//AbstractText')
        for element in abstract_elements:
            if element.text:
                abstract_text += element.text + " "

        # Add separator between abstracts for clarity during analysis
        if abstract_text:
            combined_abstract_text += f"\n\n--- ARTICLE PMID {article_pmid} | AUTHOR: {author}\nTITLE: {title}---\n{abstract_text.strip()}"

    return combined_abstract_text.strip(), pmids


def analyze_abstract(combined_abstract: str, analysis_task: str, pmids: List[str]) -> str:
    """
    Sends the combined abstract content to the Gemini API for a single, consolidated analysis.
    """
    print(f"--- Step 3: Analyze {len(pmids)} Abstracts with Gemini ---")

    if GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE" or not GEMINI_API_KEY:
        return "FATAL ERROR: Gemini API Key is missing. Cannot perform analysis."

    system_prompt = (
        "You are a concise, biomedical expert. Your summary must be objective, "
        "and synthesize the main findings from ALL provided abstracts into a single, cohesive, bulleted list. Each"
        "bullet point should be short and to the point. CITATION RULE: You MUST cite every claim using the provided SOURCE_ID (PMID)."
        "Format citations as [PMID: 1234567]. Do not invent PMIDs."
    )

    full_query = (
        f"Consolidate and address the following analysis task based on the multiple provided abstracts: {analysis_task}"
        f"\n\nCOMBINED ABSTRACT CONTENT:\n---\n{combined_abstract}"
    )

    payload = {
        "contents": [{"parts": [{"text": full_query}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]}
    }

    gemini_url_with_key = f"{GEMINI_API_URL}?key={GEMINI_API_KEY}"

    response = exponential_backoff_request(gemini_url_with_key, method="POST", payload=payload)

    if response:
        response_data = response.json()
        return response_data['candidates'][0]['content']['parts'][0]['text']
    else:
        return "An error occurred during the Gemini API call after multiple retries."

def process_metabolite(metabolite, task):
    """
    Orchestrates the full flow for a single metabolite.
    """
    print(f"\n--- Starting Analysis: {metabolite} ---")

    try:
        # 1. Get the data
        combined_abstract, pmids = fetch_abstracts(metabolite)

        if not combined_abstract:
            return f"No abstracts found for {metabolite}."

        # 2. Get the analysis
        report = analyze_abstract(combined_abstract, task, pmids)
        return report

    except Exception as e:
        return f"Error processing {metabolite}: {str(e)}"


def main():
    print_compliance_notice()

    # --- CONFIGURATION ---
    metabolites_to_process = ["L-Arginine", "Choline", "Betaine", "Creatine"]

    # The specific focus for the LLM
    analysis_task = "positive, negative, and neutral effects of metabolite on human body systems"

    # --- EXECUTION ---
    all_reports = {}

    start_time = time.time()

    for metabolite in metabolites_to_process:
        report = process_metabolite(metabolite, analysis_task)
        all_reports[metabolite] = report
        print(f"  > Result: {report}")

    end_time = time.time()

    # --- FINAL SUMMARY ---
    print("\n" + "=" * 60)
    print("BATCH PROCESSING COMPLETE")
    print(f"Total time: {round(end_time - start_time, 2)} seconds")
    print(f"Metabolites processed: {len(metabolites_to_process)}")
    print("=" * 60)

# --- Example Execution (Single Metabolite) ---
if __name__ == "__main__":
    if not NCBI_API_KEY or not GEMINI_API_KEY:
        print("ERROR: API keys not found. Check your .env file.")
    else:
        main()
