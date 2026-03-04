#!/bin/bash

# ============================================================================
# Brojač Birača po Biračkim Mestima - Izbori ID 86
# ============================================================================
# Čita biračka mesta iz polling_stations_86.json, za svako biračko mesto
# dobavlja ukupan broj birača sa API-ja, i upisuje broj nazad u JSON fajl.
# ============================================================================

BASE_URL="https://upit.birackispisak.gov.rs"
OUTPUT_DIR="./output"
TMP_DIR="./output/tmp"
INPUT_FILE="./polling_stations_86.json"
ELECTION_ID=86
COUNTS_FILE="${TMP_DIR}/counts_86.json"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

info()    { echo -e "${CYAN}ℹ${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
warn()    { echo -e "${YELLOW}⚠${NC} $1"; }
error()   { echo -e "${RED}✗${NC} $1"; }

check_dependencies() {
    local missing=()
    for cmd in curl jq sed grep; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    if [[ ${#missing[@]} -ne 0 ]]; then
        error "Nedostajući programi: ${missing[*]}"
        exit 1
    fi
}

setup_directories() {
    mkdir -p "$OUTPUT_DIR" "$TMP_DIR"
}

get_user_parameters() {
    echo ""
    echo -e "${BOLD}Unesite JMBG (13 cifara):${NC}"
    while true; do
        read -r JMBG
        [[ "$JMBG" =~ ^[0-9]{13}$ ]] && break
        warn "JMBG mora sadržati tačno 13 cifara. Pokušajte ponovo:"
    done
    echo ""
    echo -e "${BOLD}Unesite broj lične karte:${NC}"
    read -r DOCUMENT_ID
    export JMBG DOCUMENT_ID
}

# Initialize session and solve captcha (must be called before each /ListaBiraca request)
init_session() {
    COOKIE_JAR="${TMP_DIR}/cookies_$$.txt"
    local page_file="${TMP_DIR}/main_page_$$.html"

    local http_code
    http_code=$(curl -s -w "%{http_code}" \
        -c "$COOKIE_JAR" \
        -o "$page_file" \
        "${BASE_URL}/BiraciPoIzborimaIBirackimMestima")

    if [[ "$http_code" != "200" ]]; then
        error "Greška pri učitavanju početne stranice (HTTP: $http_code)"
        return 1
    fi

    local token_line
    token_line=$(grep '__RequestVerificationToken' "$page_file" | head -1)
    REQUEST_VERIFICATION_TOKEN=$(echo "$token_line" | grep -o 'value="[^"]*"' | sed 's/value="//;s/"$//')

    if [[ -z "$REQUEST_VERIFICATION_TOKEN" ]]; then
        error "Nije moguće pronaći __RequestVerificationToken"
        return 1
    fi

    local timestamp_ms
    timestamp_ms=$(($(date +%s) * 1000))
    local captcha_enc_file="${TMP_DIR}/captcha_enc_$$.txt"

    http_code=$(curl -s -w "%{http_code}" \
        -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
        -o "$captcha_enc_file" \
        "${BASE_URL}/Captcha/EncryptedCaptchaSolution?_=${timestamp_ms}")

    if [[ "$http_code" != "200" ]]; then
        error "Greška pri dobavljanju captcha (HTTP: $http_code)"
        return 1
    fi

    local encrypted_solution
    encrypted_solution=$(tr -d '"' < "$captcha_enc_file")

    if [[ -z "$encrypted_solution" ]]; then
        error "Prazno šifrovano captcha rešenje"
        return 1
    fi

    local captcha_dec_file="${TMP_DIR}/captcha_dec_$$.json"
    http_code=$(curl -s -w "%{http_code}" \
        -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
        -G \
        -o "$captcha_dec_file" \
        "${BASE_URL}/Captcha/GetCaptchaImageContent?encryptedSolution=${encrypted_solution}")

    if [[ "$http_code" != "200" ]]; then
        error "Greška pri dešifrovanju captcha (HTTP: $http_code)"
        return 1
    fi

    local captcha_attempt
    captcha_attempt=$(jq -r '.responseText' "$captcha_dec_file")

    if [[ -z "$captcha_attempt" || "$captcha_attempt" == "null" ]]; then
        error "Nije moguće dešifrovati captcha rešenje"
        return 1
    fi

    local verify_file="${TMP_DIR}/verify_$$.html"
    http_code=$(curl -s -w "%{http_code}" \
        -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
        -X POST \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -H "Origin: ${BASE_URL}" \
        -H "Referer: ${BASE_URL}/BiraciPoIzborimaIBirackimMestima" \
        --data-urlencode "__RequestVerificationToken=${REQUEST_VERIFICATION_TOKEN}" \
        --data-urlencode "JMBG=${JMBG}" \
        --data-urlencode "Document=${DOCUMENT_ID}" \
        --data-urlencode "EncrypedSolution=${encrypted_solution}" \
        --data-urlencode "Attempt=${captcha_attempt}" \
        --data-urlencode "submit=Претражи" \
        -o "$verify_file" \
        "${BASE_URL}/Verifikacija")

    if [[ "$http_code" != "302" ]]; then
        error "Greška pri verifikaciji captcha (HTTP: $http_code)"
        return 1
    fi

    export COOKIE_JAR REQUEST_VERIFICATION_TOKEN
}

# Count voters from HTML response (each voter occupies one <tr> row; first row is the header)
count_voters_from_html() {
    local html_file=$1
    local row_count
    # Count <tr> occurrences, subtract 1 for the header row
    row_count=$(grep -oi '<tr' "$html_file" | wc -l | tr -d ' ')
    echo $(( row_count > 0 ? row_count - 1 : 0 ))
}

# Fetch voter count for a single polling station; outputs the count or -1 on error
fetch_voter_count() {
    local station_id=$1
    local community_id=$2
    local response_file="${TMP_DIR}/voters_${station_id}_$$.html"

    if ! init_session; then
        rm -f "${TMP_DIR}/cookies_$$.txt" "${TMP_DIR}/main_page_$$.html" \
              "${TMP_DIR}/captcha_enc_$$.txt" "${TMP_DIR}/captcha_dec_$$.json" \
              "${TMP_DIR}/verify_$$.html"
        echo -1
        return
    fi

    local http_code
    http_code=$(curl -s -w "%{http_code}" \
        -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
        -X POST \
        -H "Referer: ${BASE_URL}/BiraciPoIzborimaIBirackimMestima" \
        --data-urlencode "__RequestVerificationToken=${REQUEST_VERIFICATION_TOKEN}" \
        --data-urlencode "MupServiceResponse=DA" \
        --data-urlencode "JMBG=${JMBG}" \
        --data-urlencode "Document=${DOCUMENT_ID}" \
        --data-urlencode "TipDokumenta=0" \
        --data-urlencode "SelectedElectionId=${ELECTION_ID}" \
        --data-urlencode "SelectedJlsId=${community_id}" \
        --data-urlencode "SelectedPollingStationsId=${station_id}" \
        -o "$response_file" \
        "${BASE_URL}/ListaBiraca")

    # Clean up temp session files
    rm -f "${TMP_DIR}/cookies_$$.txt" "${TMP_DIR}/main_page_$$.html" \
          "${TMP_DIR}/captcha_enc_$$.txt" "${TMP_DIR}/captcha_dec_$$.json" \
          "${TMP_DIR}/verify_$$.html"

    if [[ "$http_code" != "200" ]]; then
        rm -f "$response_file"
        echo -1
        return
    fi

    local count
    count=$(count_voters_from_html "$response_file")
    rm -f "$response_file"
    echo "$count"
}

cleanup() {
    echo ""
    warn "Skripta je prekinuta. Delimični rezultati sačuvani u: $COUNTS_FILE"
    # Merge whatever we have so far into the JSON before exiting
    merge_counts_into_json
    exit 1
}

# Merge the counts JSON file into the input JSON file
merge_counts_into_json() {
    if [[ ! -f "$COUNTS_FILE" ]]; then
        return
    fi

    info "Upisujem broj birača u ${INPUT_FILE}..."

    local tmp_output="${INPUT_FILE}.tmp"
    jq --slurpfile counts "$COUNTS_FILE" '
        .communities = [
            .communities[] |
            . as $community |
            .polling_stations = [
                .polling_stations[] |
                .voter_count = ($counts[0][.id] // null)
            ]
        ]
    ' "$INPUT_FILE" > "$tmp_output"

    mv "$tmp_output" "$INPUT_FILE"
    success "Podaci upisani u: $INPUT_FILE"
}

main() {
    echo -e "${CYAN}"
    echo "╔═══════════════════════════════════════════════════════════════════╗"
    echo "║              Brojač Birača - Izbori ID 86                         ║"
    echo "╚═══════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"

    check_dependencies
    setup_directories

    if [[ ! -f "$INPUT_FILE" ]]; then
        error "Ulazni fajl nije pronađen: $INPUT_FILE"
        exit 1
    fi

    get_user_parameters
    echo ""

    trap cleanup SIGINT SIGTERM

    # Load existing counts so we can resume interrupted runs
    if [[ -f "$COUNTS_FILE" ]]; then
        warn "Pronađen postojeći fajl sa rezultatima. Nastavlja se od mesta prekida."
    else
        echo '{}' > "$COUNTS_FILE"
    fi

    # Count total stations
    local total_stations
    total_stations=$(jq '[.communities[].polling_stations | length] | add' "$INPUT_FILE")
    local processed=0
    local skipped=0
    local failed=0

    info "Ukupno biračkih mesta: $total_stations"
    echo ""

    # Read all communities and stations
    local community_count
    community_count=$(jq '.communities | length' "$INPUT_FILE")

    for ((ci=0; ci<community_count; ci++)); do
        local community_id community_name station_count
        community_id=$(jq -r ".communities[$ci].id" "$INPUT_FILE")
        community_name=$(jq -r ".communities[$ci].name" "$INPUT_FILE")
        station_count=$(jq ".communities[$ci].polling_stations | length" "$INPUT_FILE")

        echo -e "${BOLD}${BLUE}── ${community_name} [${community_id}] (${station_count} BM)${NC}"

        for ((si=0; si<station_count; si++)); do
            local station_id station_name
            station_id=$(jq -r ".communities[$ci].polling_stations[$si].id" "$INPUT_FILE")
            station_name=$(jq -r ".communities[$ci].polling_stations[$si].name" "$INPUT_FILE")
            processed=$((processed + 1))

            # Skip if already counted
            local existing
            existing=$(jq -r --arg id "$station_id" '.[$id] // empty' "$COUNTS_FILE")
            if [[ -n "$existing" ]]; then
                printf "  [%d/%d] BM %s - preskočeno (već obrađeno: %s birača)\n" \
                    "$processed" "$total_stations" "$station_id" "$existing"
                skipped=$((skipped + 1))
                continue
            fi

            printf "  [%d/%d] BM %s: %s..." \
                "$processed" "$total_stations" "$station_id" "${station_name:0:60}"

            local count
            count=$(fetch_voter_count "$station_id" "$community_id")

            if [[ "$count" -eq -1 ]]; then
                echo -e " ${RED}GREŠKA${NC}"
                failed=$((failed + 1))
            else
                echo -e " ${GREEN}${count} birača${NC}"
                # Save count to the counts file
                local tmp_counts="${COUNTS_FILE}.tmp"
                jq --arg id "$station_id" --argjson count "$count" \
                    '.[$id] = $count' "$COUNTS_FILE" > "$tmp_counts"
                mv "$tmp_counts" "$COUNTS_FILE"
            fi

            sleep 0.3
        done
    done

    echo ""
    echo -e "${BOLD}${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    success "Obrađeno: $((processed - skipped - failed)) | Preskočeno: $skipped | Greška: $failed"

    merge_counts_into_json
}

main "$@"
