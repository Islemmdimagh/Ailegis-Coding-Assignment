from flask import Flask, render_template, jsonify
import threading
import time
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import os
import json
import requests
import fitz

progress_log = {}

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/progress')
def progress():
    return jsonify(progress_log)

def update_progress(url, status):
    progress_log[url] = status

def scrape_content(par_div, base_url, pdf_folder):
    subsections = []
    ignore_classes = ['contactbox', 'siblingNav', 'footer', 'header']
    ignore_titles = [
        #English
        'Contact', 'Further information', 'Related Content', 'FAQ Doctorate', 'Appointments',
        #German
        'Kontakt', 'Weitere Informationen', 'Verwandte Inhalte', 'FAQ Doktorat', 'Termine',
        #French
        'Informations complémentaires', 'Contenu associé', 'FAQ Doctorat', 'Rendez-vous',
        #Italian
        'Contatto', 'Ulteriori informazioni', 'Contenuti correlati', 'FAQ Dottorato', 'Appuntamenti'
    ]

    for content_block in par_div.find_all(['div', 'section', 'h2'], recursive=False):
        if any(cls in content_block.get('class', []) for cls in ignore_classes):
            continue

        title_tag = content_block.find(['h2', 'h3'])
        title = title_tag.get_text(strip=True) if title_tag else 'No Title'

        if any(ignore_title.lower() in title.lower() for ignore_title in ignore_titles):
            title = 'No Title'

        all_content = content_block.get_text(strip=True)
        stop_index = len(all_content)
        for ignore_title in ignore_titles:
            index = all_content.lower().find(ignore_title.lower())
            if index != -1 and index < stop_index:
                stop_index = index

        block_content = all_content[:stop_index].strip()
        attached_files = []

        for link in content_block.find_all('a', href=True):
            href = link['href']
            if href.endswith(('pdf', 'docx', 'xls')):
                file_name = link.get_text(strip=True)
                file_url = requests.compat.urljoin(base_url, href)

                if href.endswith('pdf'):
                    pdf_text = download_and_extract_pdf(file_url, pdf_folder)
                else:
                    pdf_text = None

                attached_files.append({
                    "file_name": file_name,
                    "url": file_url,
                    "content": pdf_text if pdf_text else "File content extraction not supported."
                })

        if block_content:
            subsections.append({
                "title": title,
                "url": base_url + "#" + content_block.get('id', 'unknown'),
                "content": block_content,
                "attached_files": attached_files
            })

    return subsections

def download_and_extract_pdf(pdf_url, pdf_folder):
    try:
        response = requests.get(pdf_url)
        response.raise_for_status()

        if not os.path.exists(pdf_folder):
            os.makedirs(pdf_folder)

        pdf_filename = os.path.join(pdf_folder, pdf_url.split("/")[-1])
        with open(pdf_filename, 'wb') as pdf_file:
            pdf_file.write(response.content)

        pdf_text = extract_text_from_pdf(pdf_filename)
        return pdf_text

    except Exception as e:
        print(f"Failed to download or extract PDF from {pdf_url}: {e}")
        return None

def extract_text_from_pdf(pdf_path):
    try:
        doc = fitz.open(pdf_path)
        text = ""
        for page in doc:
            text += page.get_text()
        doc.close()
        return text
    except Exception as e:
        print(f"Error extracting text from {pdf_path}: {e}")
        return "Error extracting text from PDF."

def scrape_page(page, url, visited_urls, pdf_folder):
    if url in visited_urls:
        return None

    visited_urls.add(url)

    try:
        update_progress(url, "In Progress")
        page.goto(url)
        page.wait_for_load_state('networkidle')

        html = page.content()
        soup = BeautifulSoup(html, 'html.parser')

        top_div = soup.find('div', class_='site-wrapper overviewpage')
        if not top_div:
            update_progress(url, "Failed")
            return None

        site_content_section = top_div.find('section', class_='site-content')
        if not site_content_section:
            update_progress(url, "Failed")
            return None

        content_main_section = site_content_section.find('section', class_='content-main')
        if not content_main_section:
            update_progress(url, "Failed")
            return None

        h1_title_tag = content_main_section.find_next('h1')
        main_title = h1_title_tag.get_text(strip=True) if h1_title_tag else 'No Main Title'

        parsys_div = content_main_section.find('div', class_='par parsys basecomponent')
        if parsys_div:
            content_data = scrape_content(parsys_div, url, pdf_folder)
        else:
            content_data = []

        page_data = {
            "main_title": main_title,
            "url": url,
            "content_sections": content_data,
            "subpages": []
        }

        children_nav_div = content_main_section.find('div', class_='childrenNav children basecomponent')
        if children_nav_div:
            nav_element = children_nav_div.find('nav', class_='child-nav--desktop child-nav--default child-nav')
            if nav_element:
                ul_nav_list = nav_element.find('ul', class_='child-nav__list')
                if ul_nav_list:
                    for li_item in ul_nav_list.find_all('li', class_='child-nav__item'):
                        a_tag = li_item.find('a', href=True, class_='child-nav__link')
                        if a_tag:
                            subpage_link = a_tag['href']
                            full_url = requests.compat.urljoin(url, subpage_link)

                            subpage_data = scrape_page(page, full_url, visited_urls, pdf_folder)
                            if subpage_data:
                                page_data["subpages"].append(subpage_data)

        update_progress(url, "Completed")
        return page_data

    except Exception as e:
        print(f"Error while scraping {url}: {e}")
        update_progress(url, "Failed")
        return None

def scrape_multiple_languages(language_urls):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        results_folder = 'results'
        pdf_folder = os.path.join(results_folder, 'pdfs')
        if not os.path.exists(results_folder):
            os.makedirs(results_folder)

        for language, base_url in language_urls.items():
            visited_urls = set()
            print(f"Scraping for language: {language} - URL: {base_url}")
            data = scrape_page(page, base_url, visited_urls, pdf_folder)

            if data:
                results_file_path = os.path.join(results_folder, f'data_{language}.json')
                with open(results_file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                print(f"Scraping complete for {language}. Data saved to '{results_file_path}'.")

        browser.close()

if __name__ == '__main__':
    def start_flask():
        app.run(port=5000, debug=True, use_reloader=False)

    flask_thread = threading.Thread(target=start_flask)
    flask_thread.start()

    language_urls = {
        'en': 'https://ethz.ch/en/doctorate.html',
        'de': 'https://ethz.ch/de/doktorat.html',
        'fr': 'https://ethz.ch/de/doktorat.fr.html',
        'it': 'https://ethz.ch/de/doktorat.it.html',
    }

    scrape_multiple_languages(language_urls)
