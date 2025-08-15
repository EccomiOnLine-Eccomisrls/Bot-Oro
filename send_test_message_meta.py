from wa_meta import MetaWhatsApp

if __name__ == "__main__":
    try:
        cli = MetaWhatsApp()  # prende WA_TOKEN, WA_PHONE_ID, WA_TO dall'env
        resp = cli.send_text("✅ Test WhatsApp Cloud API dal Bot Oro (Meta).")
        print("[OK] Inviato:", resp)
    except Exception as e:
        print("[ERR] Test fallito:", e)
        raise
