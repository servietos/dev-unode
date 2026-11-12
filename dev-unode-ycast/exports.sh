# YCast: Netzwerk für den vTuner-Dienst (macvlan) beim App-Start ermitteln.
#
# Umbrel sourct diese Datei auf dem Host vor jedem Start/Stopp. Sie
#   1. erkennt Adapter, Netz und Gateway über die Standardroute des Umbrel,
#   2. liest die in der Weboberfläche gespeicherte IP aus data/network.conf,
#   3. prüft sie und fällt bei Fehlern auf <Netz>.210 zurück, damit die App
#      (und damit die Weboberfläche) immer startet.
# Ergebnis: APP_YCAST_* für docker-compose.yml. Darf nie mit Fehler enden.

__ycast_ip2int() {
  local IFS=. a b c d
  read -r a b c d <<< "$1"
  echo $(( (a << 24) + (b << 16) + (c << 8) + d ))
}
__ycast_int2ip() {
  echo "$(( ($1 >> 24) & 255 )).$(( ($1 >> 16) & 255 )).$(( ($1 >> 8) & 255 )).$(( $1 & 255 ))"
}
__ycast_valid_ip() {
  [[ "$1" =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
  local o
  for o in "${BASH_REMATCH[@]:1}"; do (( 10#$o <= 255 )) || return 1; done
}

__ycast_setup() {
  local data_dir="${EXPORTS_APP_DATA_DIR:-${EXPORTS_APP_DIR:-${APP_DATA_DIR:-.}}/data}"
  local conf="${data_dir}/network.conf"
  local parent gateway cidr host_ip prefix
  local note=""

  parent="$(ip -4 route show default 2>/dev/null | awk '{for (i=1;i<NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
  gateway="$(ip -4 route show default 2>/dev/null | awk '{for (i=1;i<NF;i++) if ($i=="via") {print $(i+1); exit}}')"
  cidr="$(ip -4 -o addr show dev "${parent:-none}" 2>/dev/null | awk '{print $4; exit}')"
  host_ip="${cidr%/*}"
  prefix="${cidr#*/}"

  # Ohne erkennbares Netz: bisherige Standardwerte (192.168.178.0/24 auf enp1s0)
  if [[ -z "$parent" || -z "$gateway" ]] || ! __ycast_valid_ip "$host_ip" || ! [[ "$prefix" =~ ^[0-9]+$ ]] \
     || (( prefix < 8 || prefix > 30 )); then
    parent="enp1s0"; gateway="192.168.178.1"; host_ip=""; prefix=24
    cidr="192.168.178.0/24"
    note="Netz nicht erkannt, Standardwerte aktiv."
  fi

  local mask=$(( (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF ))
  local base_int; base_int="$(__ycast_ip2int "${host_ip:-192.168.178.0}")"
  local net=$(( base_int & mask ))
  local bcast=$(( net | (~mask & 0xFFFFFFFF) ))
  local subnet="$(__ycast_int2ip "$net")/${prefix}"

  # Standard-IP: Netzadresse + 210 (bei /24 also x.y.z.210)
  local default_ip
  default_ip="$(__ycast_int2ip $(( net + 210 )))"
  (( net + 210 < bcast )) || default_ip="$(__ycast_int2ip $(( bcast - 1 )))"

  local ip="$default_ip" wanted=""
  if [[ -f "$conf" ]]; then
    wanted="$(sed -n 's/^YCAST_IP=\([^[:space:]]*\).*/\1/p' "$conf" 2>/dev/null | head -n 1)"
  fi
  if [[ -n "$wanted" ]]; then
    if ! __ycast_valid_ip "$wanted"; then
      note="Gespeicherte IP ${wanted} ist ungültig, Standard ${default_ip} aktiv."
    else
      local w; w="$(__ycast_ip2int "$wanted")"
      if (( (w & mask) != net )); then
        note="Gespeicherte IP ${wanted} liegt nicht im Netz ${subnet}, Standard ${default_ip} aktiv."
      elif (( w == net || w == bcast )); then
        note="Gespeicherte IP ${wanted} ist Netz- oder Broadcast-Adresse, Standard ${default_ip} aktiv."
      elif [[ "$wanted" == "$gateway" || "$wanted" == "$host_ip" ]]; then
        note="Gespeicherte IP ${wanted} gehört dem Router oder dem Umbrel, Standard ${default_ip} aktiv."
      else
        ip="$wanted"
      fi
    fi
  fi

  export APP_YCAST_IP="$ip"
  export APP_YCAST_PARENT="$parent"
  export APP_YCAST_SUBNET="$subnet"
  export APP_YCAST_GATEWAY="$gateway"
  export APP_YCAST_HOST_IP="$host_ip"
  export APP_YCAST_NOTE="$note"
  # Feste MAC aus der IP (02:42 = lokal vergeben), damit der Router nicht bei jedem
  # Neustart ein „neues Gerät“ meldet; Docker würde sonst eine zufällige MAC vergeben.
  local IFS=.
  local o1 o2 o3 o4
  read -r o1 o2 o3 o4 <<< "$ip"
  export APP_YCAST_MAC="$(printf '02:42:%02x:%02x:%02x:%02x' "$o1" "$o2" "$o3" "$o4")"
}

__ycast_setup || true
unset -f __ycast_setup __ycast_ip2int __ycast_int2ip __ycast_valid_ip
