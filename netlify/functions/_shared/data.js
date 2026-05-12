// The slim API data module is built by scripts/generate_api_data.py during
// the Netlify build (see netlify.toml). It exports a flat array of stations,
// mapped + unmapped, with pre-normalized name/locality fields.
//
// Requiring it at module scope means V8 parses + caches the literal during
// Lambda's init phase (which is not billed against request latency); the
// first request to a fresh container then hits an already-built array.
const stations = require('./api_stations_data');

function loadStations() {
  return stations;
}

module.exports = { loadStations };
