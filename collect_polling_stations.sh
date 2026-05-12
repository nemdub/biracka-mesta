#!/bin/bash

# ============================================================================
# Učitavanje biračkih mesta
# ============================================================================
# Učitava sva biračka mesta za svaku opštinu/grad koji učestvuju u
# zadatim izborima i čuva ih u strukturiranom JSON formatu.
#
# Upotreba: ./collect_polling_stations.sh <ELECTION_ID>
# Output:   ./output/polling_stations_<ELECTION_ID>.json
# ============================================================================

set -e

BASE_URL="https://upit.birackispisak.gov.rs"
OUTPUT_DIR="./output"
TMP_DIR="./output/tmp"

# ── Helpers ──────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

info()    { echo -e "${CYAN}ℹ${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
warn()    { echo -e "${YELLOW}⚠${NC} $1"; }
error()   { echo -e "${RED}✗${NC} $1"; }

check_dependencies() {
    local missing=()
    for cmd in curl jq; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if [[ ${#missing[@]} -ne 0 ]]; then
        error "Nedostajući programi: ${missing[*]}"
        echo "  Ubuntu/Debian: sudo apt-get install ${missing[*]}"
        echo "  macOS:         brew install ${missing[*]}"
        exit 1
    fi
}

setup_directories() {
    mkdir -p "$OUTPUT_DIR" "$TMP_DIR"
}

# ── API helpers ───────────────────────────────────────────────────────────────

# Fetch all local localities for the election.
# Populates global arrays: locality_ids[], locality_names[]
fetch_localities() {
    local url="${BASE_URL}/PoolingStation/GetJlsForElectionId"
    local resp="${TMP_DIR}/localities_${ELECTION_ID}.json"

    local http_code
    http_code=$(curl -s -w "%{http_code}" \
        -X POST \
        -d "electionId=${ELECTION_ID}" \
        -o "$resp" \
        "$url")

    if [[ "$http_code" != "200" ]]; then
        error "Greška pri učitavanju opština/gradova (HTTP: $http_code)"
        exit 1
    fi

    locality_ids=()
    locality_names=()

    while IFS= read -r line; do locality_ids+=("$line");   done < <(jq -r '.[].Value' "$resp")
    while IFS= read -r line; do locality_names+=("$line"); done < <(jq -r '.[].Text'  "$resp")
}

# Fetch polling stations for one locality.
# Outputs a JSON array fragment suitable for embedding.
# $1 = locality_id
fetch_polling_stations_json() {
    local locality_id=$1
    local url="${BASE_URL}/PoolingStation/GetPoolingStationForJlsId"
    local resp="${TMP_DIR}/ps_${ELECTION_ID}_${locality_id}.json"

    local http_code
    http_code=$(curl -s -w "%{http_code}" \
        -X POST \
        -d "electionId=${ELECTION_ID}&jlsId=${locality_id}" \
        -o "$resp" \
        "$url")

    if [[ "$http_code" != "200" ]]; then
        echo "[]"
        warn "  Greška za opštinu ${locality_id} (HTTP: $http_code)" >&2
        return
    fi

    # Transform [{Value, Text}, ...] → [{id, name}, ...]
    jq '[.[] | {id: .Value, name: .Text}]' "$resp"
}

# ── Main ──────────────────────────────────────────────────────────────────────

cleanup() {
    echo ""
    warn "Skripta je prekinuta."
    exit 1
}
trap cleanup SIGINT SIGTERM

main() {
    if [[ -z "$1" || ! "$1" =~ ^[0-9]+$ ]]; then
        error "Upotreba: $0 <ELECTION_ID>"
        exit 1
    fi

    ELECTION_ID=$1
    OUTPUT_FILE="${OUTPUT_DIR}/polling_stations_${ELECTION_ID}.json"

    echo ""
    echo -e "${BOLD}Prikupljanje biračkih mesta${NC}"
    echo -e "Izbori ID: ${ELECTION_ID}"
    echo ""

    check_dependencies
    setup_directories

    # ── Step 1: load localities ──────────────────────────────────────────────
    info "Učitavam opštine/gradove..."
    fetch_localities

    local total_localities=${#locality_ids[@]}
    if [[ $total_localities -eq 0 ]]; then
        error "Nema dostupnih opština/gradova."
        exit 1
    fi
    success "Učitano $total_localities opština/gradova"
    echo ""

    # ── Step 2: collect polling stations per locality ────────────────────────
    info "Učitavam biračka mesta za svaku opštinu/grad..."
    echo ""

    # Build a JSON object in a temp file using jq --null-input + streaming
    # We accumulate each locality block into a bash variable (safe for typical
    # list sizes; Serbia has ~170 municipalities).

    local localities_json="["
    local first_locality=1

    for ((i = 0; i < total_localities; i++)); do
        local cid="${locality_ids[$i]}"
        local cname="${locality_names[$i]}"

        printf "  [%d/%d] %s..." "$((i + 1))" "$total_localities" "$cname"

        local ps_json
        ps_json=$(fetch_polling_stations_json "$cid")

        local ps_count
        ps_count=$(echo "$ps_json" | jq 'length')

        printf " %d biračkih mesta\n" "$ps_count"

        local locality_block
        locality_block=$(jq -n \
            --arg  id      "$cid" \
            --arg  name    "$cname" \
            --argjson stations "$ps_json" \
            '{id: $id, name: $name, polling_stations: $stations}')

        if [[ $first_locality -eq 1 ]]; then
            localities_json+="${locality_block}"
            first_locality=0
        else
            localities_json+=",${locality_block}"
        fi

        # Polite delay
        sleep 0.3
    done

    localities_json+="]"

    # ── Step 3: write final JSON ──────────────────────────────────────────────
    echo ""
    info "Zapisujem JSON fajl..."

    local localities_tmp="${TMP_DIR}/localities_combined_${ELECTION_ID}.json"
    printf '%s' "$localities_json" > "$localities_tmp"

    jq -n \
        --argjson election_id "$ELECTION_ID" \
        --slurpfile localities "$localities_tmp" \
        '{
            election: {id: $election_id},
            localities: $localities[0]
        }' > "$OUTPUT_FILE"

    # Summary
    local total_stations
    total_stations=$(jq '[.localities[].polling_stations | length] | add' "$OUTPUT_FILE")

    echo ""
    success "Završeno!"
    success "Opštine/gradovi:  $total_localities"
    success "Biračka mesta:    $total_stations"
    success "Izlazni fajl:     $OUTPUT_FILE"
    echo ""
}

main "$@"
