//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

// Processes raw central-Stockholm OSM (roads, coastline, parks) into a compact
// normalized geometry module the FAR estate map renders. Run: node scripts/geo/process-osm.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const raw = JSON.parse(readFileSync(join(here, "stockholm-osm-raw.json"), "utf8").replace(/^\uFEFF/, ""));
const elements = raw.elements ?? [];

// Bounding box used for the Overpass fetch.
const LAT0 = 59.325;
const LON0 = 18.0625;
const LAT_SPAN = 0.05;
const LON_SPAN = 0.105;
const M_PER_LAT = 111320;
const M_PER_LON = 111320 * Math.cos((LAT0 * Math.PI) / 180);

// Project lon/lat to local metres, z positive southward (matches the app's axis).
const project = ({ lat, lon }) => ({ x: (lon - LON0) * M_PER_LON, z: (LAT0 - lat) * M_PER_LAT });

function douglasPeucker(points, tolerance) {
    if (points.length < 3) return points;
    let maxDistance = 0;
    let index = 0;
    const [start, end] = [points[0], points[points.length - 1]];
    for (let i = 1; i < points.length - 1; i += 1) {
        const distance = perpendicular(points[i], start, end);
        if (distance > maxDistance) {
            maxDistance = distance;
            index = i;
        }
    }
    if (maxDistance > tolerance) {
        const left = douglasPeucker(points.slice(0, index + 1), tolerance);
        const right = douglasPeucker(points.slice(index), tolerance);
        return left.slice(0, -1).concat(right);
    }
    return [start, end];
}

function perpendicular(point, start, end) {
    const dx = end.x - start.x;
    const dz = end.z - start.z;
    const length = Math.hypot(dx, dz) || 1;
    return Math.abs((point.x - start.x) * dz - (point.z - start.z) * dx) / length;
}

// Simplify a closed ring: drop the duplicate closing vertex so DP's start/end segment is non-degenerate.
function simplifyRing(points, tolerance) {
    const open = points.length > 1 && Math.hypot(points[0].x - points[points.length - 1].x, points[0].z - points[points.length - 1].z) < 1e-6
        ? points.slice(0, -1)
        : points;
    if (open.length < 4) return open;
    return douglasPeucker(open, tolerance);
}

function ringArea(points) {
    let area = 0;
    for (let i = 0, j = points.length - 1; i < points.length; j = i, i += 1) {
        area += (points[j].x + points[i].x) * (points[j].z - points[i].z);
    }
    return area / 2;
}

// Assemble directional coastline ways into land rings by chaining on shared node ids.
function assembleCoastline(ways) {
    const remaining = new Map(ways.map((way, index) => [index, way]));
    const byStart = new Map();
    for (const [index, way] of remaining) {
        const key = way.nodes[0];
        if (!byStart.has(key)) byStart.set(key, []);
        byStart.get(key).push(index);
    }
    const rings = [];
    while (remaining.size) {
        const [firstIndex] = remaining.keys();
        const first = remaining.get(firstIndex);
        remaining.delete(firstIndex);
        const nodes = [...first.nodes];
        const coords = first.geometry.map(project);
        let guard = 0;
        while (guard++ < 5000) {
            const last = nodes[nodes.length - 1];
            if (last === nodes[0]) break;
            const candidates = (byStart.get(last) ?? []).filter((index) => remaining.has(index));
            if (!candidates.length) break;
            const nextIndex = candidates[0];
            const next = remaining.get(nextIndex);
            remaining.delete(nextIndex);
            nodes.push(...next.nodes.slice(1));
            coords.push(...next.geometry.slice(1).map(project));
        }
        rings.push({ coords, closed: nodes[0] === nodes[nodes.length - 1] });
    }
    return rings;
}

const coastlineWays = elements.filter((element) => element.tags?.natural === "coastline" && element.geometry);
const roadWays = elements.filter((element) => element.tags?.highway && element.geometry && element.geometry.length > 1);
const parkWays = elements.filter((element) => element.tags?.leisure === "park" && element.geometry && element.geometry.length > 3);

const rings = assembleCoastline(coastlineWays);

// Bounds from the fetch bbox (not raw data, which includes ways extending far outside it).
const halfXMetres = (LON_SPAN / 2) * M_PER_LON;
const halfZMetres = (LAT_SPAN / 2) * M_PER_LAT;
const scale = 0.5 / Math.max(halfXMetres, halfZMetres);
const clamp = (value) => Math.max(-0.62, Math.min(0.62, value));
const bboxNorm = { minX: -clamp(halfXMetres * scale), maxX: clamp(halfXMetres * scale), minZ: -clamp(halfZMetres * scale), maxZ: clamp(halfZMetres * scale) };
const norm = (point) => ({ x: +clamp(point.x * scale).toFixed(4), z: +clamp(point.z * scale).toFixed(4) });

// Close open coastline chains along the fetch bbox so mainland peninsulas stay solid.
const bx = bboxNorm.maxX;
const bz = bboxNorm.maxZ;
const corners = [
    { x: -bx, z: -bz }, { x: bx, z: -bz }, { x: bx, z: bz }, { x: -bx, z: bz },
];
function closeRing(points) {
    const first = points[0];
    const last = points[points.length - 1];
    if (Math.hypot(first.x - last.x, first.z - last.z) < 0.01) return points;
    // Insert the bbox corners on the outer side (larger enclosed area wins).
    const withCorners = (order) => {
        const ring = [...points];
        for (const corner of order) ring.push(corner);
        return ring;
    };
    const clockwise = withCorners(corners);
    const counter = withCorners([...corners].reverse());
    return Math.abs(ringArea(clockwise)) >= Math.abs(ringArea(counter)) ? clockwise : counter;
}

// Keep only naturally-closed coastline rings (real small islands); open mainland shore is derived from roads.
const islands = rings
    .filter((ring) => ring.closed)
    .map((ring) => simplifyRing(ring.coords.map(norm), 0.004))
    .filter((coords) => coords.length > 3 && Math.abs(ringArea(coords)) > 0.0004 && Math.abs(ringArea(coords)) < 0.25)
    .sort((a, b) => Math.abs(ringArea(b)) - Math.abs(ringArea(a)))
    .slice(0, 10)
    .map((coords) => coords.map((point) => [point.x, point.z]));

const roadClass = { motorway: 3, trunk: 3, primary: 3, secondary: 2, tertiary: 1 };
const roadLength = (points) => points.reduce((total, point, index) => (index ? total + Math.hypot(point[0] - points[index - 1][0], point[1] - points[index - 1][1]) : 0), 0);
const roads = roadWays
    .map((way) => ({
        klass: roadClass[way.tags.highway] ?? 1,
        points: douglasPeucker(way.geometry.map(project).map(norm), 0.005).map((point) => [point.x, point.z]),
    }))
    .filter((road) => road.points.length > 1 && (road.klass >= 2 || roadLength(road.points) > 0.02));

const parksSimplified = parkWays.map((way) => simplifyRing(way.geometry.map(project).map(norm), 0.0025));
const parks = parksSimplified
    .filter((coords) => coords.length > 3 && Math.abs(ringArea(coords)) > 0.0004)
    .sort((a, b) => Math.abs(ringArea(b)) - Math.abs(ringArea(a)))
    .slice(0, 48)
    .map((coords) => coords.map((point) => [point.x, point.z]));

const output = `//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------
// Generated from OpenStreetMap central-Stockholm data by scripts/geo/process-osm.mjs.
// © OpenStreetMap contributors, ODbL. Normalized to the estate-map coordinate space.

export type GeoPoint = [number, number];

export interface StockholmGeo {
    bounds: { minX: number; maxX: number; minZ: number; maxZ: number };
    islands: GeoPoint[][];
    roads: Array<{ klass: number; points: GeoPoint[] }>;
    parks: GeoPoint[][];
}

export const stockholmGeo: StockholmGeo = ${JSON.stringify(
    { bounds: { minX: +bboxNorm.minX.toFixed(4), maxX: +bboxNorm.maxX.toFixed(4), minZ: +bboxNorm.minZ.toFixed(4), maxZ: +bboxNorm.maxZ.toFixed(4) }, islands, roads, parks },
    null,
    0,
)};
`;

writeFileSync(join(here, "..", "..", "src", "lib", "stockholm-geo.ts"), output, "utf8");
console.log("islands", islands.length, "roads", roads.length, "parks", parks.length, "bbox", JSON.stringify(bboxNorm));
