const fs = require('fs');
const path = require('path');
const { normalize } = require('./translit');

// Try a handful of locations because Netlify's esbuild bundler can place
// `included_files` either alongside the function or at the project root,
// depending on bundle layout.
const CANDIDATE_PATHS = [
  path.resolve(__dirname, '../polling_stations_86.json'),
  path.resolve(__dirname, '../../polling_stations_86.json'),
  path.resolve(__dirname, '../../../polling_stations_86.json'),
  path.resolve(process.cwd(), 'polling_stations_86.json'),
];

let cachedStations = null;

function loadStations() {
  if (cachedStations) return cachedStations;

  let raw = null;
  for (const p of CANDIDATE_PATHS) {
    if (fs.existsSync(p)) {
      raw = fs.readFileSync(p, 'utf8');
      break;
    }
  }
  if (raw == null) {
    throw new Error(
      'polling_stations_86.json not found; checked: ' + CANDIDATE_PATHS.join(', ')
    );
  }

  const json = JSON.parse(raw);
  const flat = [];
  for (const loc of json.localities ?? []) {
    const localityId = loc.id;
    const localityName = loc.name;
    const localityNorm = normalize(localityName);
    for (const st of loc.polling_stations ?? []) {
      const lat = st.geo?.lat;
      const lon = st.geo?.lon;
      if (typeof lat !== 'number' || typeof lon !== 'number') continue;
      flat.push({
        id: st.id,
        name: st.name,
        localityId,
        localityName,
        lat,
        lon,
        _norm: normalize(st.name),
        _normLocality: localityNorm,
      });
    }
  }

  cachedStations = flat;
  return cachedStations;
}

module.exports = { loadStations };
