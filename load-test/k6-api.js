// k6 load tests for the polling-stations API.
//
// Usage:
//   export BASE_URL=https://<deploy-preview>.netlify.app
//   export API_KEY=k_live_...
//
//   k6 run -e PROFILE=smoke   load-test/k6-api.js
//   k6 run -e PROFILE=load    load-test/k6-api.js
//   k6 run -e PROFILE=stress  load-test/k6-api.js
//   k6 run -e PROFILE=bypass  load-test/k6-api.js
//   k6 run -e PROFILE=soak    load-test/k6-api.js
//
// Profiles:
//   smoke  - 1 RPS for 1 min, sanity-check both endpoints.
//   load   - 200 RPS for 10 min, expected election-day peak. Thresholds:
//            p95 < 200 ms, error rate < 0.1%.
//   stress - 1000 RPS for 5 min, 5x headroom, find the cliff.
//   bypass - 200 RPS for 5 min with unique cache-buster params; measures
//            true origin capacity once the CDN is taken out of the picture.
//   soak   - 100 RPS for 60 min, catches memory leaks and container churn.
//
// Run k6 from a host outside Netlify's CDN POPs (laptop, EC2, k6 Cloud).
// Running from Netlify infra would mostly measure intra-edge traffic.

import http from 'k6/http';
import { check } from 'k6';
import { Rate, Counter } from 'k6/metrics';
import { randomItem } from 'https://jslib.k6.io/k6-utils/1.4.0/index.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8888';
const API_KEY = __ENV.API_KEY || '';
const PROFILE = (__ENV.PROFILE || 'smoke').toLowerCase();

// ---------- input data ----------

// ~30 common Serbian locality / station-name fragments, mixed scripts.
const SEARCH_QUERIES = [
  'beograd', 'novi sad', 'nis', 'kragujevac', 'subotica', 'zrenjanin',
  'pancevo', 'cacak', 'kraljevo', 'leskovac', 'smederevo', 'valjevo',
  'krusevac', 'vranje', 'sabac', 'uzice', 'pozarevac', 'sombor',
  'београд', 'нови сад', 'ниш', 'крагујевац', 'суботица', 'зрењанин',
  'панчево', 'чачак', 'краљево', 'лесковац', 'ада', 'аранђеловац',
];

// Serbia bbox (matches the bbox check in api-stations-nearby.js, slightly
// narrowed to stay inside populated regions and avoid clustering on a corner).
const LAT_MIN = 42.5;
const LAT_MAX = 46.0;
const LON_MIN = 19.0;
const LON_MAX = 22.5;

const LIMITS = [1, 5, 10, 50];

function randomCoord() {
  return {
    lat: LAT_MIN + Math.random() * (LAT_MAX - LAT_MIN),
    lon: LON_MIN + Math.random() * (LON_MAX - LON_MIN),
  };
}

// ---------- profiles ----------

const PROFILES = {
  smoke: {
    scenarios: {
      both: {
        executor: 'constant-arrival-rate',
        rate: 1, timeUnit: '1s', duration: '1m',
        preAllocatedVUs: 5, maxVUs: 10,
      },
    },
    thresholds: {
      'http_req_failed': ['rate<0.01'],
      'http_req_duration': ['p(95)<500'],
    },
  },
  load: {
    scenarios: {
      both: {
        executor: 'constant-arrival-rate',
        rate: 200, timeUnit: '1s', duration: '10m',
        preAllocatedVUs: 200, maxVUs: 400,
      },
    },
    thresholds: {
      'http_req_failed': ['rate<0.001'],
      'http_req_duration': ['p(95)<200'],
    },
  },
  stress: {
    scenarios: {
      both: {
        executor: 'ramping-arrival-rate',
        startRate: 100, timeUnit: '1s',
        preAllocatedVUs: 500, maxVUs: 1500,
        stages: [
          { target: 200, duration: '30s' },
          { target: 500, duration: '1m' },
          { target: 1000, duration: '30s' },
          { target: 1000, duration: '3m' },
        ],
      },
    },
    thresholds: {
      'http_req_failed': ['rate<0.05'],
    },
  },
  bypass: {
    scenarios: {
      both: {
        executor: 'constant-arrival-rate',
        rate: 200, timeUnit: '1s', duration: '5m',
        preAllocatedVUs: 300, maxVUs: 600,
      },
    },
    thresholds: {
      'http_req_failed': ['rate<0.01'],
      'http_req_duration': ['p(95)<500'],
    },
  },
  soak: {
    scenarios: {
      both: {
        executor: 'constant-arrival-rate',
        rate: 100, timeUnit: '1s', duration: '60m',
        preAllocatedVUs: 100, maxVUs: 300,
      },
    },
    thresholds: {
      'http_req_failed': ['rate<0.001'],
      'http_req_duration': ['p(95)<200'],
    },
  },
};

if (!PROFILES[PROFILE]) {
  throw new Error(`Unknown PROFILE "${PROFILE}". One of: ${Object.keys(PROFILES).join(', ')}`);
}

export const options = PROFILES[PROFILE];

// ---------- request building ----------

const errorRate = new Rate('biracka_errors');
// Per-status-code counter — lets a failing run tell you whether it's 5xx
// (origin overload), 429 (rate-limited), or 0 (k6-side network error /
// timeout) without re-running.
const statusCounter = new Counter('biracka_status');

function buildSearchUrl(cacheBust) {
  const q = randomItem(SEARCH_QUERIES);
  const limit = randomItem(LIMITS);
  let url = `${BASE_URL}/api/stations/search?q=${encodeURIComponent(q)}&limit=${limit}`;
  if (cacheBust) url += `&_cb=${Math.random().toString(36).slice(2, 10)}`;
  return url;
}

function buildNearbyUrl(cacheBust) {
  const { lat, lon } = randomCoord();
  const limit = randomItem(LIMITS);
  let url = `${BASE_URL}/api/stations/nearby?lat=${lat.toFixed(6)}&lon=${lon.toFixed(6)}&limit=${limit}`;
  if (cacheBust) url += `&_cb=${Math.random().toString(36).slice(2, 10)}`;
  return url;
}

export default function () {
  if (!API_KEY) throw new Error('Set API_KEY env var');
  const cacheBust = PROFILE === 'bypass';
  // 70% nearby / 30% search — matches expected "stations near me" bias.
  const isNearby = Math.random() < 0.7;
  const url = isNearby ? buildNearbyUrl(cacheBust) : buildSearchUrl(cacheBust);

  const params = {
    headers: { 'X-Api-Key': API_KEY },
    // `name` collapses each endpoint to a single metric series — without it,
    // k6 keys series by URL and explodes cardinality (200k+ series at load).
    tags: {
      profile: PROFILE,
      endpoint: isNearby ? 'nearby' : 'search',
      name: isNearby ? '/api/stations/nearby' : '/api/stations/search',
    },
  };

  const res = http.get(url, params);
  const okStatus = res.status === 200;
  errorRate.add(!okStatus);
  statusCounter.add(1, {
    profile: PROFILE,
    endpoint: params.tags.endpoint,
    status: String(res.status),
  });
  check(res, {
    'status 200': (r) => r.status === 200,
    'json parses': (r) => {
      try { JSON.parse(r.body); return true; } catch (_) { return false; }
    },
    'has results array': (r) => {
      try {
        const body = JSON.parse(r.body);
        return Array.isArray(body.results) && typeof body.count === 'number';
      } catch (_) { return false; }
    },
  });
}
