import requests
import xml.etree.ElementTree as ElementTree
import os
import time
import random
import pandas as pd
import tempfile
import re
import csv
import io
from collections.abc import Iterable
from typing import List, Tuple
from dotenv import load_dotenv
from requests import Response
from enum import Enum, auto
from markdown_pdf import MarkdownPdf, Section
from MyAI import MyAI
from urllib.parse import urlencode

# --- Configuration & Setup ---
NCBI_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
HEADERS = {'Content-Type': 'application/json'}
MAX_ARTICLES = 100
AMINO_ACIDS = {'Ala', 'Arg', 'Asn', 'Asp', 'Cys',
               'Glu', 'Gln', 'Gly', 'His', 'Ile',
               'Leu', 'Lys', 'Met', 'Phe', 'Pro',
               'Ser', 'Thr', 'Trp', 'Tyr', 'Val'}

load_dotenv()
NCBI_API_KEY = os.environ.get("NCBI_API_KEY")

model_name = "gemini-flash-lite-latest"

system_prompt = (
    "You are a biomedical literature assistant specializing in metabolite research. "
    "Synthesize the provided PubMed abstracts into a concise, objective summary of the "
    "biological function of each metabolite. Treat the abstracts as the evidence base; "
    "do not present a reported association as proof of causation or a finding in one species "
    "as established in all species.\n\n"
    "### REQUIRED FORMAT ###\n"
    "Return exactly one concise sentence and no heading or other commentary.\n"
    "Summarize the most relevant metabolite functions supported by the abstracts.\n"
    "Include one to three plain-text PMID citations only when they directly support "
    "a metabolite function described in the sentence, using this format: [PMID: 1234567].\n\n"
    "- Cite only abstracts that directly support a relevant function. Do not cite abstracts "
    "merely because they were provided, and never list PMIDs without an associated claim.\n"
    "- If multiple abstracts support a relevant function, use a separate citation for each source "
    "(e.g., [PMID: 27090191], [PMID: 2382591]).\n"
    "- If the abstracts do not support a function relevant to the search focus, state that "
    "briefly in the single sentence and include no citations (e.g., The provided abstracts do "
    "not contain biological functions specifically addressing {keyword} in relation to {metabolite}.)\n"
    "### EVIDENCE AND STYLE RULES ###\n"
    "- Synthesize the main relevant themes across the abstracts rather than listing "
    "individual findings or abstracts.\n"
    "- If a search focus or keywords are provided, prioritize relevant findings only "
    "when supported by the abstracts. If they do not address the focus, state this "
    "concisely; do not infer the requested property from the search terms alone.\n"
    "- Never invent or guess a PMID or other detail.\n"
    "- If findings are mixed or context-dependent, convey the key qualification briefly "
    "within the sentence.\n"
    "- If the abstracts do not support a clear biological role or finding, say so in one "
    "sentence rather than inventing a conclusion.\n"
    "- Do not infer that a metabolite has no effect merely because the provided abstracts "
    "do not report one.\n"
    "- Be concise and factual. Omit introductory or closing commentary."
)

my_ai = MyAI(model_name=model_name,system_prompt=system_prompt)

class Method(Enum):
    SEARCH = auto()
    FETCH = auto()

def exponential_backoff_request(url: str, method: str = "GET", payload: dict = None, max_retries: int = 5) -> (
        requests.Response | None):
    """
        Makes an HTTP request with exponential backoff for handling rate limits (429) and server errors.
    """
    last_exception = None
    for attempt in range(max_retries):
        try:
            if method == "GET":
                response = requests.get(url)
            else:
                response = requests.post(url, headers=HEADERS, json=payload)

            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                return response

            if response.status_code == 429:
                print(f"RATE LIMIT HIT (429) on attempt {attempt + 1}. Retrying...")
            else:  # 5xx errors
                print(f"Server Error ({response.status_code}) on attempt {attempt + 1}. Retrying...")

            delay = (2 ** attempt) + random.uniform(0, 1)
            print(f"Waiting for {delay:.2f} seconds before retry.")
            time.sleep(delay)

        except requests.exceptions.RequestException as e:
            last_exception = e
            print(f"Request failed: {e}. Retrying...")
            delay = (2 ** attempt) + random.uniform(0, 1)
            time.sleep(delay)


    raise RuntimeError(f"Failed to retrieve data after {max_retries} attempts.")

def get_metabolites() -> List[str]:
    metabolite_file = pd.read_csv("metabolite_list_small.csv")
    metabolite_df = pd.DataFrame(metabolite_file)
    return metabolite_df['Metabolites'].tolist()

def build_pubmed_query(metabolite: str, keyword: str = "") -> str:
    """Build a PubMed query for a metabolite and optional keywords."""

    def as_title_abstract_phrase(term: str) -> str:
        # Remove surrounding quotes and any internal quotes so the term stays
        # a single phrase in the generated query.
        phrase = term.strip().strip('"').replace('"', "")
        return f'"{phrase}"[tiab]'

    metabolite_term = as_title_abstract_phrase(metabolite)
    keywords = [
        term.strip()
        for term in re.split(r"[,;]", keyword or "")
        if term.strip()
    ]

    if not keywords:
        return metabolite_term

    keyword_terms = " OR ".join(
        as_title_abstract_phrase(term)
        for term in keywords
    )
    return f"({metabolite_term}) AND ({keyword_terms})"

def create_url(
    query_value: str,
    keyword: str = "",
    method: Method = Method.SEARCH,
) -> str:
    """Create an ESearch or EFetch URL for PubMed."""

    if method == Method.FETCH:
        params = {
            "db": "pubmed",
            "id": query_value,
            "retmode": "xml",
            "rettype": "abstract",
        }
        endpoint = "efetch.fcgi"
    else:
        params = {
            "db": "pubmed",
            "term": build_pubmed_query(query_value, keyword),
            "retmode": "json",
            "retmax": MAX_ARTICLES,
            "sort": "relevance",
        }
        endpoint = "esearch.fcgi"

    if NCBI_API_KEY:
        params["api_key"] = NCBI_API_KEY

    return f"{NCBI_BASE_URL}{endpoint}?{urlencode(params)}"

def is_peptide(metabolite: str) -> bool:
    return all(part in AMINO_ACIDS
               for part in metabolite.split("-"))

def normalize_peptide_notation(abstract:str) -> str:
    normalized = abstract
    normalized = normalized.replace("·", "-")
    normalized = normalized.replace("–", "-")
    normalized = normalized.replace("—", "-")
    normalized = normalized.replace("−", "-")
    return normalized

# Check if abstracts contain peptide of interest
def is_valid_abstract(abstract: str, peptide: str) -> bool:
    parts = abstract.split(peptide)
    beg = parts[0]
    end = parts[-1]

    if len(parts) == 2:
        if (beg == "" or beg[-1]) != "-" and (end == "" or end[0] != "-"):
            return True
        return False
    else:
        for i in range(len(parts)-1):
            front = parts[i]
            back = parts[i+1]
            if front[-1] != "-" and back[0] != "-":
                return True
        return False

def build_abstract_corpus(response: Response, metabolite:str) -> str:
    """
    Extracts info from each article to create a string of combined abstracts
    :param metabolite: str
    :param response: Response
    :return: str
    """
    root = ElementTree.fromstring(response.text)
    combined_abstract_text = ""
    seen_titles = set()

    for article in root.findall('.//PubmedArticle'):
        pmid_el = article.find(".//PMID")
        article_pmid = pmid_el.text if pmid_el is not None else "UnknownPMID"

        title_el = article.find(".//ArticleTitle")
        title = title_el.text if title_el is not None else "No Title"

        if title in seen_titles:
            continue

        seen_titles.add(title)

        author_el = article.find(".//Author/LastName")
        author = author_el.text if author_el is not None else "Unknown"

        abstract_text = ""
        abstract_elements = article.findall('.//AbstractText')
        peptide = is_peptide(metabolite)
        for element in abstract_elements:
            if element.text:
                abstract_text += element.text + " "
        if peptide:
            normalized_abstract = normalize_peptide_notation(abstract_text)
            if not is_valid_abstract(normalized_abstract, metabolite):
                continue
        if abstract_text:
            combined_abstract_text += f"\n\n--- ARTICLE PMID {article_pmid} | AUTHOR: {author}\nTITLE: {title}---\n{abstract_text.strip()}"

    return combined_abstract_text

def fetch_abstracts(metabolite: str, keyword: str) -> Tuple[str, List[str]] | None:
    """
    Executes ESearch and EFetch to retrieve abstracts for the top MAX_ARTICLES results.
    Returns a single concatenated abstract string and a list of PMIDs.
    """
    esearch_url = create_url(metabolite, keyword, Method.SEARCH)
    response = exponential_backoff_request(esearch_url)
    if not response:
        return "", []

    search_data = response.json()
    pmids = search_data['esearchresult']['idlist']
    if not pmids:
        return "", []

    pmid_list_str = ",".join(pmids)
    efetch_url = create_url(pmid_list_str, keyword, Method.FETCH)
    response = exponential_backoff_request(efetch_url)
    if not response:
        return "", []

    combined_abstract_text = build_abstract_corpus(response, metabolite)
    included_pmids = re.findall(r"--- ARTICLE PMID (\d+)", combined_abstract_text)
    return combined_abstract_text.strip(), included_pmids

def analyze_abstract(combined_abstracts: str, task: str) -> str:
    input_text = task + combined_abstracts
    return my_ai.generate_response(input_text=input_text)

def link_valid_pmids(text: str, pmids: Iterable[str]) -> str:
    """Link plain PMID citations when their IDs are in the supplied PMID list."""

    valid_pmids = {str(pmid).strip() for pmid in pmids}
    citation_pattern = re.compile(
        r"\[((?:PMID:\s*\d+)(?:\s*,\s*PMID:\s*\d+)*)\](?!\s*\()",
        re.IGNORECASE,
    )

    def replace_group(match: re.Match) -> str:
        citations = re.findall(r"PMID:\s*(\d+)", match.group(1), re.IGNORECASE)
        linked_citations = []

        for pmid in citations:
            citation = f"[PMID: {pmid}]"
            if pmid in valid_pmids:
                citation = (
                    f"[PMID: {pmid}]"
                    f"(https://pubmed.ncbi.nlm.nih.gov/{pmid}/)"
                )
            linked_citations.append(citation)

        return ", ".join(linked_citations)

    return citation_pattern.sub(replace_group, text)

def process_metabolite(metabolite: str, task: str, keyword: str) -> dict:
    """
    Analyze one metabolite and return a structured result record.
    """
    try:
        combined_abstract, pmids = fetch_abstracts(metabolite, keyword)
        if not combined_abstract:
            return {
                "metabolite": str(metabolite),
                "summary": "No PubMed abstracts were retrieved for this metabolite and search focus.",
                "status": "no_results",
                "pmids": [],
            }
        summary = analyze_abstract(combined_abstract, task).strip()
        return {
            "metabolite": str(metabolite),
            "summary": summary,
            "status": "complete",
            "pmids": pmids,
        }

    except Exception as e:
        return {
            "metabolite": str(metabolite),
            "summary": f"Processing failed: {str(e)}",
            "status": "error",
            "pmids": [],
        }

def build_markdown_report(results: list[dict]) -> str:
    """Format structured metabolite results as Markdown for display and PDF."""
    report = "# Metabolite Report\n"
    for result in results:
        metabolite = result.get("metabolite", "Unknown metabolite")
        summary = result.get("summary", "")
        pmids = result.get("pmids", [])
        linked_summary = link_valid_pmids(summary, pmids)
        report += f"\n## {metabolite}\n\n{linked_summary}\n"
    return report

def get_csv_bytes(results: list[dict]) -> bytes:
    """Serialize structured metabolite results as UTF-8 CSV bytes."""
    output = io.StringIO(newline="")
    fieldnames = ["Metabolite", "Summary", "Status", "PMIDs"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for result in results:
        summary = result.get("summary", "")
        allowed_pmids = {str(pmid).strip() for pmid in result.get("pmids", [])}
        cited_pmids = list(dict.fromkeys(
            pmid for pmid in re.findall(r"PMID:\s*(\d+)", summary, re.IGNORECASE)
            if pmid in allowed_pmids
        ))
        # Ensure CSV values remain plain text if the model unexpectedly returns Markdown links.
        summary = re.sub(
            r"\[PMID:\s*(\d+)\]\(https://pubmed\.ncbi\.nlm\.nih\.gov/\1/?\)",
            r"[PMID: \1]",
            summary,
            flags=re.IGNORECASE,
        )
        writer.writerow({
            "Metabolite": result.get("metabolite", ""),
            "Summary": summary,
            "Status": result.get("status", "complete"),
            "PMIDs": "; ".join(cited_pmids),
        })

    return output.getvalue().encode("utf-8-sig")

def write_report(report: str, file_name: str):
    with open(f"{file_name}.md", "w", encoding="utf-8") as md_file:
        md_file.write(report)
    with open(f"{file_name}.md", "r", encoding="utf-8") as md_file:
        markdown_content = md_file.read()

    pdf = MarkdownPdf()
    pdf.add_section(Section(markdown_content))
    pdf.save(f"{file_name}.pdf")

def get_pdf_bytes(report_text: str):
    """Generates a PDF from markdown and returns the raw bytes."""
    pdf = MarkdownPdf()
    pdf.add_section(Section(report_text))

    # Permission fix: Use a manual path to ensure file is closed
    fd, temp_path = tempfile.mkstemp(suffix=".pdf")
    try:
        os.close(fd)  # Close the file descriptor immediately
        pdf.save(temp_path)  # Let the library write to the closed path

        with open(temp_path, "rb") as f:
            pdf_bytes = f.read()

        return pdf_bytes
    finally:
        # Now it is safe to remove because no process is holding the file
        if os.path.exists(temp_path):
            os.remove(temp_path)

def analyze_metabolite(metabolite: str, keyword: str) -> dict:
    if not NCBI_API_KEY:
        return {
            "metabolite": str(metabolite),
            "summary": "API keys not found. Check your .env file.",
            "status": "error",
            "pmids": [],
        }
    else:
        analysis_task = (
                f"Summarize the biological roles or findings supported by the provided PubMed "
                f"abstracts for the metabolite {metabolite}."
                + (f" Focus on this search topic: {keyword}." if keyword and keyword.strip() else "")
                + " Follow the required format and evidence rules in your system instructions. "
                  "Use only the provided abstracts as evidence."
        )
        return process_metabolite(metabolite, analysis_task, keyword)

def main(metabolite: str, keyword: str):
    return analyze_metabolite(metabolite, keyword)

if __name__ == "__main__":
    pass
