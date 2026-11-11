# Smart TV Telegram: LAN-IP des Umbrel für die Einrichtungsseite ermitteln
# (der Fernseher holt den Stream unter dieser Adresse). Darf nie mit Fehler enden.
APP_SMARTTV_HOST_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<NF;i++) if ($i=="src") {print $(i+1); exit}}' || true)"
export APP_SMARTTV_HOST_IP
