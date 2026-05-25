import { useMemo, useEffect } from "react";
import { MapContainer, TileLayer, CircleMarker, Circle, Tooltip, useMap, useMapEvents } from "react-leaflet";
import "leaflet/dist/leaflet.css";

// City -> [lat, lon] for the locations the pipeline produces. The
// scraper writes plain-English strings ("Bay Area", "Remote, US",
// "London, UK"), so we use a substring-match table rather than
// trying to geocode every variant.  Hits ~95% of real postings;
// anything we miss falls into the "unmapped" count badge.
const CITY_COORDS = {
  // North America
  "san francisco": [37.7749, -122.4194],
  "bay area": [37.7749, -122.4194],
  "palo alto": [37.4419, -122.143],
  "mountain view": [37.3861, -122.0839],
  "menlo park": [37.4529, -122.1817],
  "sunnyvale": [37.3688, -122.0363],
  "santa clara": [37.3541, -121.9552],
  "san jose": [37.3382, -121.8863],
  "cupertino": [37.323, -122.0322],
  "los angeles": [34.0522, -118.2437],
  "irvine": [33.6846, -117.8265],
  "santa monica": [34.0195, -118.4912],
  "long beach": [33.7701, -118.1937],
  "san diego": [32.7157, -117.1611],
  "oakland": [37.8044, -122.2712],
  "berkeley": [37.8715, -122.273],
  "fremont": [37.5483, -121.9886],
  "redwood city": [37.4852, -122.2364],
  "foster city": [37.5585, -122.2711],
  "san mateo": [37.5630, -122.3255],
  "south san francisco": [37.6547, -122.4077],
  "los altos": [37.3852, -122.1141],
  "milpitas": [37.4323, -121.8996],
  "burlingame": [37.5841, -122.3661],
  "hayward": [37.6688, -122.0808],
  "seattle": [47.6062, -122.3321],
  "redmond": [47.674, -122.1215],
  "bellevue": [47.6101, -122.2015],
  "portland": [45.5152, -122.6784],
  "denver": [39.7392, -104.9903],
  "boulder": [40.015, -105.2705],
  "austin": [30.2672, -97.7431],
  "dallas": [32.7767, -96.797],
  "houston": [29.7604, -95.3698],
  "atlanta": [33.749, -84.388],
  "chicago": [41.8781, -87.6298],
  "minneapolis": [44.9778, -93.265],
  "boston": [42.3601, -71.0589],
  "cambridge": [42.3736, -71.1097],
  "new york": [40.7128, -74.006],
  "nyc": [40.7128, -74.006],
  "brooklyn": [40.6782, -73.9442],
  "manhattan": [40.7831, -73.9712],
  "jersey city": [40.7178, -74.0431],
  "philadelphia": [39.9526, -75.1652],
  "washington": [38.9072, -77.0369],
  "arlington": [38.8816, -77.091],
  "raleigh": [35.7796, -78.6382],
  "miami": [25.7617, -80.1918],
  "toronto": [43.6532, -79.3832],
  "vancouver": [49.2827, -123.1207],
  "montreal": [45.5017, -73.5673],
  "remote, us": [39.8283, -98.5795],   // geographic centre of contiguous US
  "remote us": [39.8283, -98.5795],
  // Europe
  "london": [51.5074, -0.1278],
  "manchester": [53.4808, -2.2426],
  "edinburgh": [55.9533, -3.1883],
  "dublin": [53.3498, -6.2603],
  "paris": [48.8566, 2.3522],
  "amsterdam": [52.3676, 4.9041],
  "berlin": [52.52, 13.405],
  "munich": [48.1351, 11.582],
  "zurich": [47.3769, 8.5417],
  "stockholm": [59.3293, 18.0686],
  "copenhagen": [55.6761, 12.5683],
  "barcelona": [41.3851, 2.1734],
  "madrid": [40.4168, -3.7038],
  "lisbon": [38.7223, -9.1393],
  "remote, eu": [50.1109, 8.6821],     // ~Frankfurt, central Europe
  "remote eu": [50.1109, 8.6821],
  // Asia / APAC
  "tokyo": [35.6762, 139.6503],
  "singapore": [1.3521, 103.8198],
  "hong kong": [22.3193, 114.1694],
  "seoul": [37.5665, 126.978],
  "bangalore": [12.9716, 77.5946],
  "bengaluru": [12.9716, 77.5946],
  "hyderabad": [17.385, 78.4867],
  "mumbai": [19.076, 72.8777],
  "delhi": [28.6139, 77.209],
  "tel aviv": [32.0853, 34.7818],
  "sydney": [-33.8688, 151.2093],
  "melbourne": [-37.8136, 144.9631],
};

export function locateJob(loc) {
  if (!loc || typeof loc !== "string") return null;
  const lc = loc.toLowerCase();
  // Substring match — cities first by length descending so "san francisco"
  // beats "san" when both appear in the table.
  const keys = Object.keys(CITY_COORDS).sort((a, b) => b.length - a.length);
  for (const k of keys) {
    if (lc.includes(k)) return CITY_COORDS[k];
  }
  return null;
}

// Haversine distance in km between two [lat, lon] points. Used by the
// matches table to filter on the user's pinned area without round-tripping
// through the backend.
export function haversineKm(a, b) {
  if (!a || !b) return Infinity;
  const R = 6371;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(b[0] - a[0]);
  const dLon = toRad(b[1] - a[1]);
  const lat1 = toRad(a[0]);
  const lat2 = toRad(b[0]);
  const x =
    Math.sin(dLat / 2) ** 2 +
    Math.sin(dLon / 2) ** 2 * Math.cos(lat1) * Math.cos(lat2);
  return 2 * R * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x));
}

// Click-to-set pin handler. Only mounted in filter mode.
function PinSetter({ onSet }) {
  useMapEvents({
    click(e) {
      onSet([e.latlng.lat, e.latlng.lng]);
    },
  });
  return null;
}

// Fit-bounds helper. Only re-runs when the marker set changes so
// panning around doesn't get yanked back to a fit on every render.
function FitBounds({ points }) {
  const map = useMap();
  useEffect(() => {
    if (!points.length) return;
    if (points.length === 1) {
      map.setView(points[0], 4);
      return;
    }
    const lats = points.map((p) => p[0]);
    const lons = points.map((p) => p[1]);
    map.fitBounds(
      [[Math.min(...lats), Math.min(...lons)], [Math.max(...lats), Math.max(...lons)]],
      { padding: [20, 20], maxZoom: 5 },
    );
  }, [points.length]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

export default function JobMap({
  matches,
  theme,
  height = 280,
  // Filter mode: parent owns the pin list; we emit add/remove events.
  // `pins` is an array of [lat, lon]. A click on the map fires
  // onPinAdd(latLon); click on an existing pin fires onPinRemove(idx).
  // `onPinsClear` clears the whole list. `radiusKm` is the shared
  // radius for every pin (job passes if within ANY pin's circle).
  filterMode = false,
  pins = [],
  radiusKm = 50,
  onPinAdd,
  onPinRemove,
  onPinsClear,
}) {
  // Bucket matches by [lat,lon] so a city with 12 jobs renders one big
  // marker, not 12 overlapping pins. Marker radius scales with count
  // (sqrt for visual area linearity).
  const { buckets, unmapped } = useMemo(() => {
    const buckets = new Map();
    let unmapped = 0;
    for (const m of matches || []) {
      const coords = locateJob(m.location || m._location);
      if (!coords) {
        unmapped += 1;
        continue;
      }
      const k = `${coords[0]},${coords[1]}`;
      const b = buckets.get(k) || { coords, jobs: [] };
      b.jobs.push(m);
      buckets.set(k, b);
    }
    return { buckets: [...buckets.values()], unmapped };
  }, [matches]);

  const points = buckets.map((b) => b.coords);
  const total = matches?.length || 0;
  const t = theme || {};

  // In filter mode the map is the active control — render it even
  // when there are zero matches so the user can drop a pin before
  // any cycle has run.
  if (!total && !filterMode) {
    return (
      <div style={{ background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px", padding: "20px", textAlign: "center", color: t.textDim, fontSize: "13px" }}>
        No matches yet — run a cycle to populate the map.
      </div>
    );
  }

  return (
    <div style={{ background: t.bgAlt, border: `1px solid ${t.border}`, borderRadius: "4px", overflow: "hidden" }}>
      <div style={{ padding: "10px 14px", borderBottom: `1px solid ${t.border}`, display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: "10px", letterSpacing: "1.5px", textTransform: "uppercase", color: t.textDim, fontWeight: 600 }}>
          {filterMode
            ? (pins.length === 0 ? "Click the map to add a pin" : `Location filter — ${pins.length} pin${pins.length === 1 ? "" : "s"}`)
            : "Where the jobs are"}
        </div>
        <div style={{ fontSize: "11px", color: t.textFaint }}>
          {filterMode && pins.length > 0 ? (
            <span>
              {radiusKm} km radius · <button onClick={onPinsClear} style={{ background: "none", border: "none", color: t.accent, cursor: "pointer", padding: 0, fontSize: "11px", textDecoration: "underline" }}>clear all</button>
            </span>
          ) : !filterMode && (
            <>
              {buckets.length} cities · {total - unmapped}/{total} mapped
              {unmapped > 0 && <span title="Locations the geocoder didn't recognise (e.g. 'Remote', 'Anywhere'). Add the city to JobMap.jsx CITY_COORDS to map them."> · {unmapped} unmapped</span>}
            </>
          )}
        </div>
      </div>
      <div style={{ height: `${height}px` }}>
        <MapContainer
          center={[30, 0]}
          zoom={2}
          minZoom={2}
          maxZoom={10}
          style={{ height: "100%", width: "100%" }}
          worldCopyJump
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          {buckets.map((b, i) => {
            const r = 5 + Math.sqrt(b.jobs.length) * 3;
            return (
              <CircleMarker
                key={i}
                center={b.coords}
                radius={r}
                pathOptions={{
                  color: t.accent || "#e85d4a",
                  fillColor: t.accent || "#e85d4a",
                  fillOpacity: 0.55,
                  weight: 1.5,
                }}
              >
                <Tooltip direction="top" offset={[0, -r]}>
                  <div style={{ fontSize: "12px", fontWeight: 600 }}>
                    {b.jobs[0].location || "—"} ({b.jobs.length})
                  </div>
                  <div style={{ fontSize: "11px", marginTop: "4px", maxWidth: "240px" }}>
                    {b.jobs.slice(0, 5).map((j, k) => (
                      <div key={k} style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                        · {j.title} <span style={{ opacity: 0.7 }}>@ {j.company}</span>
                      </div>
                    ))}
                    {b.jobs.length > 5 && <div style={{ opacity: 0.7 }}>… +{b.jobs.length - 5} more</div>}
                  </div>
                </Tooltip>
              </CircleMarker>
            );
          })}
          {filterMode && (
            <PinSetter onSet={onPinAdd} />
          )}
          {filterMode && pins.map((p, i) => (
            <span key={i}>
              <Circle
                center={p}
                radius={radiusKm * 1000}
                pathOptions={{
                  color: t.accent || "#e85d4a",
                  fillColor: t.accent || "#e85d4a",
                  fillOpacity: 0.12,
                  weight: 1.5,
                }}
              />
              <CircleMarker
                center={p}
                radius={9}
                // bubblingMouseEvents=false stops the click from
                // propagating to the MapContainer, which otherwise
                // immediately fires the PinSetter click handler and
                // adds a new pin at the same spot — net effect: the
                // remove "doesn't work." DOM-level stopPropagation
                // alone wasn't enough because Leaflet uses its own
                // event-routing layer beneath the DOM event.
                bubblingMouseEvents={false}
                eventHandlers={{
                  click() {
                    if (onPinRemove) onPinRemove(i);
                  },
                }}
                pathOptions={{
                  color: t.accent || "#e85d4a",
                  fillColor: "#fff",
                  fillOpacity: 1,
                  weight: 2.5,
                }}
              >
                <Tooltip direction="top" offset={[0, -10]}>
                  <div style={{ fontSize: "11px", fontWeight: 600 }}>
                    Pin #{i + 1} · click to remove
                  </div>
                </Tooltip>
              </CircleMarker>
            </span>
          ))}
          {!filterMode && <FitBounds points={points} />}
        </MapContainer>
      </div>
    </div>
  );
}
