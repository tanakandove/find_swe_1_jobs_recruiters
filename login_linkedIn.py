from playwright.sync_api import sync_playwright

def save_linkedin_session():
    """Launch a browser and Linked sign in/Login using Google"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.linkedin.com/login")

        #delay so that user can login into LinkedIn
        #google details below
        print("Login using Google")
        input("Press ENTER here AFTER you see yourself logged in on LinkedIn...")

        # Save cookies + localStorage to a file
        #we will use this session for scrapping
        context.storage_state(path="linkedin_state.json")
        print("LinkedIn session saved to linkedin_state.json")

        browser.close()

if __name__ == "__main__":
    save_linkedin_session()
