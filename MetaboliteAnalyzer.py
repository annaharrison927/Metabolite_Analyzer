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
MAX_ARTICLES = 3

# API Keys
load_dotenv()
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Check if keys are available for a clean startup
if not NCBI_API_KEY or not GEMINI_API_KEY:
    print("FATAL ERROR: API keys not found. Please ensure you have set them in your environment or a local .env file.")
    # Exit gracefully if keys are missing
    exit()

# --- Helper Function for Rate Limiting ---
def exponential_backoff_request(url: str, max_retries: int = 5) -> requests.Response | None:
    """
    Makes an HTTP GET request with exponential backoff for handling rate limits (429).
    """
    for attempt in range(max_retries):
        try:
            response = requests.get(url)

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


def get_pubmed_abstracts(search_term: str) -> Tuple[str, List[str]]:
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
        abstract_text = ""
        # Look for AbstractText within the current article
        abstract_elements = article.findall('.//AbstractText')
        for element in abstract_elements:
            if element.text:
                abstract_text += element.text + " "

        # Add separator between abstracts for clarity during analysis
        article_pmid = article.find('.//PMID').text
        if abstract_text:
            combined_abstract_text += f"\n\n--- ARTICLE PMID {article_pmid} ---\n{abstract_text.strip()}"

    return combined_abstract_text.strip(), pmids


def analyze_abstract_with_gemini(combined_abstract: str, analysis_task: str, pmids: List[str]) -> str:
    """
    Sends the combined abstract content to the Gemini API for a single, consolidated analysis.
    """
    print(f"--- Step 3: Analyze {len(pmids)} Abstracts with Gemini ---")

    if GEMINI_API_KEY == "YOUR_GEMINI_API_KEY_HERE" or not GEMINI_API_KEY:
        return "FATAL ERROR: Gemini API Key is missing. Cannot perform analysis."

    system_prompt = (
        "You are a concise, biomedical expert. Your summary must be objective, "
        "and synthesize the main findings from ALL provided abstracts into a single, cohesive, bulleted list. Each"
        "bullet point should be short and to the point."
    )

    full_query = (
        f"Consolidate and address the following analysis task based on the multiple provided abstracts: {analysis_task}"
        f"\n\nCOMBINED ABSTRACT CONTENT:\n---\n{combined_abstract}"
    )

    payload = {
        "contents": [{"parts": [{"text": full_query}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]}
    }

    try:
        response = requests.post(f"{GEMINI_API_URL}?key={GEMINI_API_KEY}", headers=HEADERS, json=payload)
        response.raise_for_status()

        response_data = response.json()

        return response_data['candidates'][0]['content']['parts'][0]['text']

    except requests.exceptions.RequestException as e:
        return f"An error occurred during the Gemini API call: {e}"

def print_compliance_notice():
    print("-" * 60)
    print("NCBI COMPLIANCE NOTICE")
    print("This tool uses NCBI E-utilities. By proceeding, you agree to")
    print("NCBI's Disclaimer and Copyright notice:")
    print("https://www.ncbi.nlm.nih.gov/About/disclaimer.html")
    print("-" * 60)
    print()

# --- Example Execution (Single Metabolite) ---
if __name__ == "__main__":
    print_compliance_notice()

    # Single metabolite and analysis task
    metabolite_to_analyze = "Serotonin"
    analysis_task = "Give me a list of characteristics of this metabolite associated with human health, synthesizing findings from all sources."

    print("=" * 60)
    print(f"STARTING CONSOLIDATED ANALYSIS FOR: {metabolite_to_analyze} ({MAX_ARTICLES} articles)")

    # 1. Fetch combined abstracts and PMIDs
    combined_abstract, pmids = get_pubmed_abstracts(metabolite_to_analyze)

    final_result = {
        "Metabolite": metabolite_to_analyze,
        "PMIDs_Used": pmids,
        "Analysis_Result": "Analysis Failed"
    }

    if pmids:
        # 2. Analyze the combined text
        analysis_result = analyze_abstract_with_gemini(
            combined_abstract=combined_abstract,
            analysis_task=analysis_task,
            pmids=pmids
        )
        final_result["Analysis_Result"] = analysis_result
    else:
        final_result["Analysis_Result"] = combined_abstract  # Contains the error message

    print("\n\n" + "#" * 60)
    print("FINAL CONSOLIDATED RESULT SUMMARY")
    print("#" * 60)
    print(json.dumps(final_result, indent=2))