from playwright.sync_api import sync_playwright


def get_job_description(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(url, timeout=60000)

        # wait for page to fully load
        page.wait_for_load_state("networkidle")

        content = page.content()

        browser.close()

    return content


if __name__ == "__main__":
    url = "https://hiring.cafe/viewjob/k5ligbx3dagxydyh"
    html = get_job_description(url)
    print(html[:2000])  # print first 2000 chars
