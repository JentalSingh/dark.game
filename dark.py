#!/usr/bin/env python
# coding: utf-8

"""
Dark Gaming Forum – Full Sign‑up with Selenium Wire + Proxy Rotation + Cloudflare
- Opens the forum thread URL first
- Clicks the "Sign Up" button from the thread page
- Handles Cloudflare challenges automatically
- Auto-generates email: random_string@gmail.com
- Auto-generates username and password
- Fills exact form fields (Email, Username, Password)
- Submits immediately without CAPTCHA check
- Prints email, username, password on success
"""

import json
import os
import random
import re
import string
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from seleniumwire import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    NoSuchElementException,
    JavascriptException,
    WebDriverException,
    StaleElementReferenceException,
    ElementClickInterceptedException,
)
from dotenv import load_dotenv
from faker import Faker

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================
TARGET_URL = "https://forum.dark-gaming.com/t/complete-flight-booking-guide-american-airlines-reservations-online-booking-low-fares-budget-travel-hacks/38791"
PROXY_FILE = Path("proxies.txt")
TWO_CAPTCHA_API_KEY = os.getenv("TWO_CAPTCHA_API_KEY", "")
PAGE_LOAD_WAIT = 10
MAX_USERNAME_LENGTH = 16

if TWO_CAPTCHA_API_KEY and len(TWO_CAPTCHA_API_KEY) == 32:
    print(f"🔑 2Captcha API Key loaded: {TWO_CAPTCHA_API_KEY[:4]}...{TWO_CAPTCHA_API_KEY[-4:]}")
else:
    print("⚠️ 2Captcha API Key is missing or invalid – Turnstile solving will fail.")

# ============================================================
# TURNSTILE INTERCEPT SCRIPT (CDP)
# ============================================================
TURNSTILE_INTERCEPT_SCRIPT = """
(() => {
  if (window.__tsInterceptorInstalled) return;
  window.__tsInterceptorInstalled = true;
  window.__tsParams = null;
  window.__tsCallback = null;
  console.clear = () => console.log("Console was cleared");
  const patch = () => {
    if (!window.turnstile || typeof window.turnstile.render !== "function" || window.turnstile.__codexPatched) return false;
    const originalRender = window.turnstile.render.bind(window.turnstile);
    window.turnstile.render = (container, options = {}) => {
      window.__tsParams = {
        sitekey: options.sitekey || null,
        pageurl: window.location.href,
        data: options.cData || null,
        pagedata: options.chlPageData || null,
        action: options.action || null,
        userAgent: navigator.userAgent,
        json: 1
      };
      window.cfCallback = typeof options.callback === "function" ? options.callback : null;
      console.log("intercepted-params:" + JSON.stringify(window.__tsParams));
      return originalRender(container, options);
    };
    window.turnstile.__codexPatched = true;
    return true;
  };
  const timer = setInterval(() => { if (patch()) clearInterval(timer); }, 50);
  setTimeout(() => clearInterval(timer), 20000);
})();
"""

# ============================================================
# PROXY LOADING & PARSING
# ============================================================
def load_proxies():
    proxies = []
    if PROXY_FILE.exists():
        with PROXY_FILE.open("r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    proxies.append(line)
    return proxies

def build_proxy_config(proxy_value):
    if not proxy_value:
        return None
    if "://" not in proxy_value and ":" in proxy_value:
        parts = proxy_value.split(":")
        if len(parts) == 2:
            host, port = parts
            return {"host": host, "port": int(port), "username": "", "password": "", "label": f"{host}:{port}"}
        elif len(parts) == 4:
            host, port, username, password = parts
            return {"host": host, "port": int(port), "username": username, "password": password, "label": f"{host}:{port}"}
    parsed = urlparse(proxy_value if "://" in proxy_value else f"http://{proxy_value}")
    if not parsed.hostname or not parsed.port:
        return None
    return {
        "host": parsed.hostname,
        "port": parsed.port,
        "username": parsed.username or "",
        "password": parsed.password or "",
        "label": f"{parsed.hostname}:{parsed.port}",
    }

def get_proxy_candidates(limit=20):
    proxies = load_proxies()
    if not proxies:
        print("⚠️ No proxies found – using direct connection.")
        return [None]
    random.shuffle(proxies)
    candidates = []
    for p in proxies[:limit]:
        cfg = build_proxy_config(p)
        if cfg:
            candidates.append(cfg)
    if not candidates:
        candidates = [None]
    return candidates

# ============================================================
# DRIVER CREATION (Selenium Wire)
# ============================================================
def create_driver(proxy_config):
    chrome_options = webdriver.ChromeOptions()
    chrome_options.page_load_strategy = "none"
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--no-default-browser-check")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--start-maximized")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    
    chrome_options.set_capability("goog:loggingPrefs", {"browser": "ALL"})

    seleniumwire_options = {}
    if proxy_config:
        host = proxy_config["host"]
        port = proxy_config["port"]
        username = proxy_config.get("username")
        password = proxy_config.get("password")
        proxy_url = f"http://{host}:{port}"
        if username and password:
            proxy_url = f"http://{username}:{password}@{host}:{port}"
        elif username:
            proxy_url = f"http://{username}@{host}:{port}"
        seleniumwire_options = {
            "proxy": {
                "http": proxy_url,
                "https": proxy_url,
                "no_proxy": "localhost,127.0.0.1"
            },
            "verify_ssl": False,
        }
        print(f"✅ Proxy configured: {host}:{port}")

    driver = webdriver.Chrome(
        options=chrome_options,
        seleniumwire_options=seleniumwire_options
    )
    driver.implicitly_wait(10)
    driver.set_page_load_timeout(30)

    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": TURNSTILE_INTERCEPT_SCRIPT})
    
    return driver

# ============================================================
# IP CHECKER
# ============================================================
def check_browser_ip(driver):
    print("🌐 Checking browser public IP...")
    try:
        driver.get("https://api.ipify.org?format=json")
        time.sleep(2)
        body = driver.find_element(By.TAG_NAME, "body").text.strip()
        data = json.loads(body)
        ip = data.get("ip", "unknown")
        print(f"🌐 Browser public IP: {ip}")
        return ip
    except Exception as e:
        print(f"⚠️ IP check failed: {e}")
        return None
    finally:
        driver.get("about:blank")
        time.sleep(1)

# ============================================================
# CLOUDFLARE CHALLENGE DETECTION & SOLVER
# ============================================================
def is_cloudflare_challenge(driver):
    try:
        title = (driver.title or "").lower()
    except:
        title = ""
    try:
        body = driver.find_element(By.TAG_NAME, "body").text.lower()
    except:
        body = ""
    
    markers = [
        "just a moment",
        "performing security verification",
        "checking your browser",
        "verify you are human",
        "this website uses a security service",
        "ray id:",
        "performance and security by cloudflare",
    ]
    return any(m in title for m in markers) or any(m in body for m in markers)

def drain_browser_logs(driver):
    intercepted = None
    try:
        entries = driver.get_log("browser")
    except Exception:
        return None
    for entry in entries:
        message = entry.get("message", "")
        if "intercepted-params:" in message:
            try:
                log_entry = message.encode("utf-8").decode("unicode_escape")
            except Exception:
                log_entry = message
            match = re.search(r'intercepted-params:({.*?})', log_entry)
            if match:
                try:
                    intercepted = json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
        if "turnstile" in message.lower() or "cloudflare" in message.lower() or "403" in message:
            print("Browser console:", message)
    return intercepted

def extract_turnstile_from_page(driver):
    try:
        params = driver.execute_script("return window.__tsParams;")
        if params and params.get("sitekey"):
            return params
    except (JavascriptException, WebDriverException):
        pass
    try:
        element = driver.find_element(By.CSS_SELECTOR, ".cf-turnstile,[data-sitekey]")
        sitekey = element.get_attribute("data-sitekey")
        if sitekey:
            return {
                "sitekey": sitekey,
                "pageurl": driver.current_url,
                "data": element.get_attribute("data-cdata"),
                "pagedata": None,
                "action": element.get_attribute("data-action"),
                "userAgent": driver.execute_script("return navigator.userAgent;"),
                "json": 1,
            }
    except Exception:
        pass
    try:
        iframes = driver.find_elements(By.CSS_SELECTOR, "iframe[src*='turnstile']")
    except Exception:
        iframes = []
    for iframe in iframes:
        src = iframe.get_attribute("src") or ""
        query = parse_qs(urlparse(src).query)
        sitekey = (query.get("sitekey") or query.get("k") or [None])[0]
        if sitekey:
            return {
                "sitekey": sitekey,
                "pageurl": driver.current_url,
                "data": None,
                "pagedata": None,
                "action": None,
                "userAgent": driver.execute_script("return navigator.userAgent;"),
                "json": 1,
            }
    return None

def wait_for_turnstile_params(driver, timeout_seconds=30):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        intercepted = drain_browser_logs(driver)
        if intercepted and intercepted.get("sitekey"):
            print("Captured Turnstile params from browser logs")
            return intercepted
        params = extract_turnstile_from_page(driver)
        if params and params.get("sitekey"):
            print("Captured Turnstile params from page state")
            return params
        time.sleep(1)
    return None

def solve_turnstile_2captcha(params):
    if not TWO_CAPTCHA_API_KEY or len(TWO_CAPTCHA_API_KEY) != 32:
        raise RuntimeError("TWO_CAPTCHA_API_KEY invalid")
    payload = {
        "key": TWO_CAPTCHA_API_KEY,
        "method": "turnstile",
        "sitekey": params["sitekey"],
        "pageurl": params["pageurl"],
        "json": 1,
    }
    if params.get("action"):
        payload["action"] = params["action"]
    if params.get("data"):
        payload["data"] = params["data"]
    if params.get("pagedata"):
        payload["pagedata"] = params["pagedata"]
    if params.get("userAgent"):
        payload["useragent"] = params["userAgent"]
    
    print(f"🔄 Submitting Turnstile to 2Captcha for {params['pageurl']}")
    response = requests.post("https://2captcha.com/in.php", data=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    if data.get("status") != 1:
        raise RuntimeError(f"2Captcha submit failed: {data}")
    captcha_id = data["request"]
    print(f"✅ 2Captcha accepted request id: {captcha_id}")
    
    for attempt in range(1, 31):
        time.sleep(5)
        poll = requests.get(
            "https://2captcha.com/res.php",
            params={"key": TWO_CAPTCHA_API_KEY, "action": "get", "id": captcha_id, "json": 1},
            timeout=60,
        )
        poll.raise_for_status()
        result = poll.json()
        if result.get("status") == 1:
            token = result.get("request")
            if token:
                print(f"✅ Received 2Captcha token on attempt {attempt}")
                return token
        elif result.get("request") == "CAPCHA_NOT_READY":
            print(f"⏳ 2Captcha still solving ({attempt}/30)")
        else:
            raise RuntimeError(f"2Captcha poll failed: {result}")
    raise TimeoutError("2Captcha timeout")

def apply_turnstile_token(driver, token):
    print("🔄 Applying Turnstile token")
    result = driver.execute_script(
        """
        const solveToken = arguments[0];
        if (typeof window.cfCallback === "function") {
            window.cfCallback(solveToken);
            return "callback";
        }
        let applied = false;
        document.querySelectorAll('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]').forEach((el) => {
            el.value = solveToken;
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            applied = true;
        });
        return applied ? "input" : "none";
        """,
        token,
    )
    print(f"✅ Applied Turnstile token via '{result}' mode")

def wait_for_challenge_clear(driver, timeout_seconds=20):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            current_url = driver.current_url
            title = (driver.title or "").strip().lower()
            page_source = (driver.page_source or "").lower()
        except:
            time.sleep(0.5)
            continue
        challenge_markers = (
            "__cf_chl_rt_tk=" in current_url
            or "just a moment" in title
            or "cf-challenge-running" in page_source
            or "challenge-form" in page_source
            or "why_captcha" in page_source
        )
        if not challenge_markers:
            print("✅ Challenge cleared.")
            return True
        time.sleep(1)
    print("❌ Challenge may not have cleared within timeout.")
    return False

def manual_captcha_wait():
    print("\n🔴 Please solve the CAPTCHA manually in the browser.")
    input("🟢 Press ENTER after solving...")
    print("✅ Continuing.")

def handle_cloudflare_challenge(driver):
    if not is_cloudflare_challenge(driver):
        print("✅ No Cloudflare challenge detected initially.")
        return True
    
    print("🛡️ Cloudflare challenge page detected.")
    
    MAX_CF_REFRESHES = 2
    for refresh_attempt in range(MAX_CF_REFRESHES + 1):
        if not is_cloudflare_challenge(driver):
            print("✅ Cloudflare challenge cleared after refresh.")
            return True
        if refresh_attempt >= MAX_CF_REFRESHES:
            print("❌ Cloudflare challenge still active after refresh retries.")
            break
        print(f"🔄 Refreshing page ({refresh_attempt + 1}/{MAX_CF_REFRESHES})...")
        driver.refresh()
        try:
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except Exception:
            pass
        time.sleep(3)
    
    if is_cloudflare_challenge(driver):
        print("🛡️ Attempting to solve with Turnstile + 2Captcha...")
        params = wait_for_turnstile_params(driver, timeout_seconds=30)
        if params:
            print("✅ Turnstile params captured. Solving with 2Captcha...")
            try:
                token = solve_turnstile_2captcha(params)
                apply_turnstile_token(driver, token)
                if wait_for_challenge_clear(driver, timeout_seconds=30):
                    print("✅ Challenge cleared after solving.")
                    time.sleep(3)
                    return True
                else:
                    print("❌ Challenge did not clear after applying token.")
                    manual_captcha_wait()
                    if not is_cloudflare_challenge(driver):
                        print("✅ Manual intervention cleared the challenge.")
                        return True
                    else:
                        return False
            except Exception as e:
                print(f"⚠️ Turnstile solving failed: {e}")
                traceback.print_exc()
        else:
            print("ℹ️ No Turnstile params found.")
    
    print("⚠️ Automated solving failed or unavailable. Falling back to manual...")
    manual_captcha_wait()
    if not is_cloudflare_challenge(driver):
        print("✅ Manual intervention cleared the challenge.")
        return True
    else:
        print("❌ Challenge still present after manual wait.")
        return False

# ============================================================
# DARK GAMING – THREAD → SIGNUP
# ============================================================

def click_signup_button(driver):
    """Thread page se 'Sign Up' button click karega."""
    print("🔘 Looking for 'Sign Up' button on thread page...")
    
    selectors = [
        "a.sign-up-button",
        "a[class*='sign-up']",
        "//a[contains(@class, 'sign-up-button')]",
        "//a[contains(text(), 'Sign Up')]",
        "//button[contains(text(), 'Sign Up')]",
        "a.btn-primary:has-text('Sign Up')",
    ]
    
    for selector in selectors:
        try:
            if selector.startswith("//"):
                elements = driver.find_elements(By.XPATH, selector)
            else:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
            
            for el in elements:
                if el.is_displayed() and el.is_enabled():
                    print(f"✅ Found Sign Up button: {selector}")
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                    time.sleep(0.5)
                    try:
                        el.click()
                    except:
                        driver.execute_script("arguments[0].click();", el)
                    print("✅ Sign Up button clicked!")
                    time.sleep(3)
                    return True
        except Exception as e:
            print(f"⚠️ Selector failed: {selector} - {e}")
            continue
    
    # Fallback: direct signup page
    print("⚠️ Sign Up button not found. Trying direct signup URL...")
    try:
        driver.get("https://forum.dark-gaming.com/signup")
        time.sleep(3)
        return True
    except Exception as e:
        print(f"❌ Failed to open signup page: {e}")
        return False

def fill_registration_form(driver, username, email, password):
    """
    Dark Gaming registration form fill karega.
    Fields based on screenshot:
      - Email: id="new-account-email"
      - Username: id="new-account-username"
      - Password: id="new-account-password"
      - Name (optional) – skip
      - In-game Name (optional) – skip
    """
    print("✍️ Filling registration form...")
    
    # Wait for form to load
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.ID, "new-account-email"))
        )
    except TimeoutException:
        print("⚠️ Email field not found, waiting for page to settle...")
        time.sleep(5)
    
    # Email
    try:
        email_el = driver.find_element(By.ID, "new-account-email")
        email_el.clear()
        email_el.send_keys(email)
        print(f"   ✅ Filled Email: {email}")
    except Exception as e:
        print(f"   ❌ Could not fill Email: {e}")
        return False

    # Username
    try:
        user_el = driver.find_element(By.ID, "new-account-username")
        user_el.clear()
        user_el.send_keys(username)
        print(f"   ✅ Filled Username: {username}")
    except Exception as e:
        print(f"   ❌ Could not fill Username: {e}")
        return False

    # Password
    try:
        pass_el = driver.find_element(By.ID, "new-account-password")
        pass_el.clear()
        pass_el.send_keys(password)
        print("   ✅ Filled Password")
    except Exception as e:
        print(f"   ❌ Could not fill Password: {e}")
        return False

    # Optional fields: Name and In-game Name – skip

    return True

def click_signup_submit_button(driver):
    """Sign Up submit button click karega."""
    print("🔘 Looking for Sign Up submit button...")
    selectors = [
        "button[type='submit']",
        "//button[contains(text(), 'Sign Up')]",
        "//input[@type='submit' and @value='Sign Up']",
        "//button[contains(@class, 'btn-primary')]",
        "button.signup-page-cta__signup",
    ]
    for selector in selectors:
        try:
            if selector.startswith("//"):
                btn = driver.find_element(By.XPATH, selector)
            else:
                btn = driver.find_element(By.CSS_SELECTOR, selector)
            if btn.is_displayed() and btn.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.5)
                try:
                    btn.click()
                except:
                    driver.execute_script("arguments[0].click();", btn)
                print("✅ Sign Up button clicked.")
                return True
        except Exception:
            continue
    print("❌ Could not find Sign Up submit button.")
    return False

def handle_verification(driver):
    """Verification link paste karne ke liye prompt."""
    print("\n" + "="*60)
    print("📧 Verification email sent to your email address.")
    print("Please check your email and paste the verification link below.")
    print("="*60)
    verification_link = input("🔗 Paste verification link (or press ENTER to skip): ").strip()
    
    if verification_link:
        print(f"\n🌐 Opening verification link: {verification_link}")
        try:
            driver.get(verification_link)
            time.sleep(5)
            print("✅ Verification link opened.")
            driver.save_screenshot("dark_gaming_verification_done.png")
            print("📸 Screenshot saved: dark_gaming_verification_done.png")
            return True
        except Exception as e:
            print(f"⚠️ Error opening verification link: {e}")
            return False
    else:
        print("ℹ️ Verification skipped.")
        return False

# ============================================================
# DATA GENERATION
# ============================================================
def generate_random_string(length=10):
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))

def generate_email():
    return f"{generate_random_string(10)}@gmail.com"

def generate_username():
    fake = Faker()
    base = fake.user_name()[:MAX_USERNAME_LENGTH]
    if len(base) < 4:
        base = base + generate_random_string(4)
    return base[:MAX_USERNAME_LENGTH]

def generate_password():
    fake = Faker()
    password = fake.password(
        length=14,
        special_chars=True,
        digits=True,
        upper_case=True,
        lower_case=True
    )
    return password

# ============================================================
# MAIN AUTOMATION
# ============================================================
def run_automation(proxy_config):
    driver = create_driver(proxy_config)
    try:
        # ---- CHECK BROWSER IP ----
        check_browser_ip(driver)
        
        # Generate user data
        username = generate_username()
        email = generate_email()
        password = generate_password()
        
        print("\n🧑 Generated user data:")
        print(f"   Username: {username}")
        print(f"   Email: {email}")
        print(f"   Password: {password}")
        
        # ---- STEP 1: THREAD URL OPEN ----
        print(f"\n🌐 Opening thread URL: {TARGET_URL}")
        driver.get(TARGET_URL)
        time.sleep(5)
        
        # ---- HANDLE CLOUDFLARE ----
        if not handle_cloudflare_challenge(driver):
            print("❌ Cloudflare challenge could not be resolved. Aborting.")
            driver.save_screenshot("dark_gaming_cloudflare_failed.png")
            return False
        
        print(f"⏳ Waiting {PAGE_LOAD_WAIT} seconds for page to settle...")
        time.sleep(PAGE_LOAD_WAIT)
        
        # ---- STEP 2: CLICK SIGN UP ----
        if not click_signup_button(driver):
            print("❌ Could not click Sign Up button.")
            return False
        
        # ---- HANDLE CLOUDFLARE AGAIN (if any) ----
        if not handle_cloudflare_challenge(driver):
            print("❌ Cloudflare on signup page could not be resolved.")
            return False
        
        # ---- STEP 3: FILL FORM ----
        if not fill_registration_form(driver, username, email, password):
            print("❌ Form filling failed.")
            return False
        
        # ---- STEP 4: DIRECT SUBMIT (NO CAPTCHA CHECK) ----
        # Sab kuch fill ho chuka hai, ab seedha Sign Up click karo
        if not click_signup_submit_button(driver):
            print("❌ Could not submit registration.")
            return False
        
        # ---- STEP 5: WAIT ----
        print("⏳ Waiting for registration to process...")
        time.sleep(5)
        driver.save_screenshot("dark_gaming_registration_submitted.png")
        print("📸 Screenshot saved: dark_gaming_registration_submitted.png")
        
        # ---- STEP 6: VERIFICATION ----
        handle_verification(driver)
        
        # ---- SUCCESS ----
        print("\n" + "="*60)
        print("🎉 REGISTRATION COMPLETED!")
        print("="*60)
        print(f"📧 Email    : {email}")
        print(f"👤 Username : {username}")
        print(f"🔑 Password : {password}")
        print("="*60)
        return True
        
    except Exception as e:
        print(f"❌ Automation failed: {e}")
        traceback.print_exc()
        driver.save_screenshot("dark_gaming_error.png")
        return False
    finally:
        input("\n⏸️ Press ENTER to close browser...")
        driver.quit()

# ============================================================
# MAIN LOOP – PROXY ROTATION
# ============================================================
def main():
    print("\n" + "="*70)
    print("🚀 DARK GAMING FORUM SIGN-UP (THREAD → SIGNUP)")
    print("="*70)
    print(f"🌐 Target URL: {TARGET_URL}")
    print("📧 Email format: random_string@gmail.com (auto-generated)")
    print(f"👤 Max username length: {MAX_USERNAME_LENGTH} characters")
    
    candidates = get_proxy_candidates(limit=20)
    for i, proxy_config in enumerate(candidates, 1):
        print(f"\n🔁 Attempt {i} using {proxy_config['label'] if proxy_config else 'Direct connection'}")
        success = run_automation(proxy_config)
        if success:
            print("\n✅ SUCCESS! Registration completed.")
            return
        else:
            print("\n❌ This attempt failed. Trying next proxy...")
    print("\n❌ All attempts failed.")

if __name__ == "__main__":
    main()