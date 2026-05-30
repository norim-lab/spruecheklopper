import asyncio
import nodriver as uc
import time

async def test():
    TARGET_URL = "https://www.sprachnudel.de"
    
    print("Starte Chrome (sichtbar)...")
    browser = await uc.start(headless=False)
    print("Chrome gestartet!")
    
    for i in range(3):
        print(f"\nTest {i+1}: Lade sprachnudel.de...")
        page = await browser.get(TARGET_URL)
        await asyncio.sleep(5)
        
        text = await page.get_content()
        if "Just a moment" in text or "Nur einen Moment" in text:
            print("  CF-Challenge erkannt - warte auf Loesung...")
            for j in range(30):
                await asyncio.sleep(2)
                try:
                    text = await page.get_content()
                    if "Just a moment" not in text and "Nur einen Moment" not in text:
                        print(f"  Geloest nach {(j+1)*2}s!")
                        break
                except:
                    pass
            else:
                print("  NICHT geloest!")
                continue
        
        if "Reimw" in text:
            print(f"  ERFOLG! Seite geladen ({len(text)} bytes)")
            break
        else:
            print(f"  Unbekannt: Laenge={len(text)}")
    
    print("\nBeendet.")
    await browser.stop()

if __name__ == "__main__":
    asyncio.run(test())
