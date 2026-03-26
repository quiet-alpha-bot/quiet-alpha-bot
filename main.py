def fetch_uw():
    url = "https://api.unusualwhales.com/api/option-alerts"
    headers = {
        "Authorization": f"Bearer {UW_API_KEY}",
        "Accept": "application/json"
    }

    try:
        r = requests.get(url, headers=headers, timeout=10)

        print("UW status:", r.status_code)
        print("UW raw:", r.text[:300])

        data = r.json()

        if not data or "data" not in data or len(data["data"]) == 0:
            return None

        alert = data["data"][0]

        ticker = alert.get("ticker", "UNKNOWN")
        side = alert.get("side", "UNKNOWN")
        premium = alert.get("premium", 0)

        return ticker, side, premium

    except Exception as e:
        send_telegram(f"UW Error: {e}")
        return None
