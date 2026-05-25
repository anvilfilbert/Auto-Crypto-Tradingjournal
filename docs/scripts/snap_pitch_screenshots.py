"""Snap fresh screenshots for the PDF brochure."""
from playwright.sync_api import sync_playwright
from pathlib import Path
import time

OUT = Path("/tmp/pdf_screenshots")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://192.168.1.21:8082/training"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1280, "height": 850}, device_scale_factor=2)
    page = ctx.new_page()

    # 1. Path view — show the hero tier tree
    page.goto(BASE + "/", wait_until="networkidle", timeout=20000)
    time.sleep(1.5)
    page.screenshot(path=str(OUT / "01-path-hero.png"), full_page=False)
    print("  ✓ 01-path-hero")

    # 2. Lesson view with diagram (candle anatomy)
    page.goto(BASE + "/lesson/12-candle-anatomy", wait_until="networkidle", timeout=20000)
    time.sleep(1.5)
    # Scroll so the diagram is in view
    page.evaluate("window.scrollTo(0, 380)")
    time.sleep(0.5)
    page.screenshot(path=str(OUT / "02-lesson-with-diagram.png"), full_page=False)
    print("  ✓ 02-lesson-with-diagram")

    # 3. Lesson showing tables (worked example)
    page.goto(BASE + "/lesson/07-position-sizing", wait_until="networkidle", timeout=20000)
    time.sleep(1.5)
    page.evaluate("window.scrollTo(0, 800)")
    time.sleep(0.5)
    page.screenshot(path=str(OUT / "03-lesson-tables.png"), full_page=False)
    print("  ✓ 03-lesson-tables")

    # 4. Quiz view (questions visible)
    page.goto(BASE + "/quiz/12-candle-anatomy", wait_until="networkidle", timeout=20000)
    time.sleep(1.5)
    page.screenshot(path=str(OUT / "04-quiz-view.png"), full_page=False)
    print("  ✓ 04-quiz-view")

    # 5. Wyckoff lesson (advanced content with diagram)
    page.goto(BASE + "/lesson/33-wyckoff-phases", wait_until="networkidle", timeout=20000)
    time.sleep(1.5)
    page.evaluate("window.scrollTo(0, 400)")
    time.sleep(0.5)
    page.screenshot(path=str(OUT / "05-wyckoff-diagram.png"), full_page=False)
    print("  ✓ 05-wyckoff-diagram")

    # 6. Trade Apgar (T6 with scoreboard)
    page.goto(BASE + "/lesson/46-trade-apgar", wait_until="networkidle", timeout=20000)
    time.sleep(1.5)
    page.evaluate("window.scrollTo(0, 600)")
    time.sleep(0.5)
    page.screenshot(path=str(OUT / "06-apgar-scoreboard.png"), full_page=False)
    print("  ✓ 06-apgar-scoreboard")

    browser.close()
print("\nDone.")
