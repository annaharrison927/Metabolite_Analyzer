# PubMed Metabolite Analyzer (NCBI & Gemini)

This project provides an automated Python pipeline for querying the NCBI PubMed database, retrieving multiple abstracts for a given metabolite, and using the Gemini API for synthesized, structured analysis.

This tool is optimized for speed and relevance, allowing for high-throughput screening of metabolite literature.

# Getting Started

## 1. Project Setup (PyCharm Workflow)

This project must be run inside a dedicated Python Virtual Environment (venv) to avoid dependency conflicts.

New Project: When creating the project in PyCharm, ensure you select "New environment using Virtualenv" and CHECK the "Create Git repository" box.

Add Files: Place the following files in the root of your new project directory:

`MetaboliteAnalyzer.py` (The main script)

`requirements.txt`

`.env` (Secrets file)

`.gitignore` (In the root directory)

## 2. Dependency Installation

Open the PyCharm integrated terminal (ensure you see (venv) in the prompt) and install the necessary libraries:

`pip install -r requirements.txt`

## 3. API Key Configuration (Crucial Security Step)

This project utilizes API keys from two sources (Gemini and NCBI). Your API keys must be kept separate from your code. The script reads them securely from the `.env` file.

Create the `.env` file in the root directory.

Populate the file with your actual keys (do not use quotes if the key contains no spaces/special characters, but it's safest to use quotes as a best practice):

# Usage & Tuning

The analysis parameters are controlled within the `if __name__ == "__main__"`: block of the `MetaboliteAnalyzer.py` script. This will be changed to a more user-friendly control later.

## 1. Key Variables

`metabolites_to_process` is the list of molecules to be analyzed. In updated versions, this will likely come from a csv file.

`analysis_task` is the instruction for the LLM on what to extract (e.g., "focusing on neurological effects"). This directs the focus of the literature search and synthesis.

`MAX_ARTICLES` is the number of abstracts (N) retrieved per metabolite. Adjust this value to balance analysis depth (more articles) vs. processing speed (fewer articles).

## 2. Throughput Guidance

| `MAX_ARTICLES` Value | Est. Throughput (Per Hour) |
|----------------------|----------------------------|
| 5                    | $\approx 360 - 600$        |
| 10                   | $\approx 240 - 360$        |
| 20                   | $\approx 144 - 240$        |
| 50                   | $\approx 80 - 144$         |
| 100                  | $\approx 40 - 80$          |

## 3. Running the Analysis
This project is tracked using Git. The following configuration files ensure security and reproducibility:

`.gitignore`: Prevents secrets (`.env`) and large dependency files (`venv/`) from being pushed to GitHub.

`requirements.txt`: Lists dependencies for easy setup on any new machine.

# Disclaimer and Compliance
This project utilizes NCBI's E-utilities, using a rate-limiting function to ensure courteous methods of data extraction. By using this tool, you are agreeing to NCBI's Disclaimer and Copyright notice, found here:
https://www.ncbi.nlm.nih.gov/About/disclaimer.html