//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { stockholmGeo, type GeoPoint } from "./stockholm-geo";

export interface CampusPosition {
    x: number;
    y: number;
    z: number;
}

export interface CampusBounds {
    width: number;
    depth: number;
}

export interface CampusRoad {
    klass: number;
    points: Array<[number, number]>;
}

export interface CampusLabel {
    name: string;
    x: number;
    z: number;
}

export type CampusDetailLevel = "full" | "reduced" | "minimal";

export function getCampusDetailLevel(workspaceCount: number): CampusDetailLevel {
    if (workspaceCount > 240) return "minimal";
    if (workspaceCount > 60) return "reduced";
    return "full";
}

// Real neighbourhood label anchors (normalized) for map legibility only.
const districtLabels: CampusLabel[] = [
    { name: "VASASTAN", x: -0.05, z: -0.34 },
    { name: "NORRMALM", x: -0.02, z: -0.12 },
    { name: "KUNGSHOLMEN", x: -0.32, z: -0.02 },
    { name: "ÖSTERMALM", x: 0.16, z: -0.14 },
    { name: "GÄRDET", x: 0.34, z: -0.2 },
    { name: "GAMLA STAN", x: 0.02, z: 0.05 },
    { name: "DJURGÅRDEN", x: 0.26, z: 0.06 },
    { name: "SÖDERMALM", x: 0.0, z: 0.26 },
    { name: "HORNSTULL", x: -0.26, z: 0.24 },
    { name: "LILJEHOLMEN", x: -0.34, z: 0.38 },
    { name: "JOHANNESHOV", x: 0.12, z: 0.4 },
    { name: "NACKA", x: 0.4, z: 0.3 },
];

const bounds = stockholmGeo.bounds;
const NORM_WIDTH = bounds.maxX - bounds.minX;
const NORM_DEPTH = bounds.maxZ - bounds.minZ;

// Land mask rasterized once from the real road network (roads exist only on developed land).
const MASK_W = 300;
const MASK_H = Math.round(MASK_W * (NORM_DEPTH / NORM_WIDTH));
const CELL_W = NORM_WIDTH / MASK_W;
const CELL_H = NORM_DEPTH / MASK_H;
const LAND_RADIUS = 0.024;
// Elliptical core: trims ragged edges into a softer, rounder silhouette.
const CORE_RX = 0.47;
const CORE_RZ = 0.44;
let landMask: Uint8Array | null = null;

function buildLandMask(): Uint8Array {
    const mask = new Uint8Array(MASK_W * MASK_H);
    const radiusCellsX = Math.ceil(LAND_RADIUS / CELL_W);
    const radiusCellsY = Math.ceil(LAND_RADIUS / CELL_H);
    const stamp = (nx: number, nz: number) => {
        const cx = Math.floor((nx - bounds.minX) / CELL_W);
        const cy = Math.floor((nz - bounds.minZ) / CELL_H);
        for (let dy = -radiusCellsY; dy <= radiusCellsY; dy += 1) {
            for (let dx = -radiusCellsX; dx <= radiusCellsX; dx += 1) {
                const x = cx + dx;
                const y = cy + dy;
                if (x < 0 || y < 0 || x >= MASK_W || y >= MASK_H) continue;
                mask[y * MASK_W + x] = 1;
            }
        }
    };
    for (const road of stockholmGeo.roads) {
        for (let index = 1; index < road.points.length; index += 1) {
            const [ax, az] = road.points[index - 1];
            const [bx, bz] = road.points[index];
            const distance = Math.hypot(bx - ax, bz - az);
            const steps = Math.max(1, Math.ceil(distance / (LAND_RADIUS * 0.6)));
            for (let step = 0; step <= steps; step += 1) {
                const t = step / steps;
                stamp(ax + (bx - ax) * t, az + (bz - az) * t);
            }
        }
    }
    // Fill gaps between parallel streets, trim to the core ellipse, then round the coastline.
    const dilate = (source: Uint8Array) => {
        const out = new Uint8Array(source.length);
        for (let y = 0; y < MASK_H; y += 1) {
            for (let x = 0; x < MASK_W; x += 1) {
                let on = source[y * MASK_W + x];
                for (let dy = -1; dy <= 1 && !on; dy += 1) {
                    for (let dx = -1; dx <= 1; dx += 1) {
                        const nx = x + dx;
                        const ny = y + dy;
                        if (nx >= 0 && ny >= 0 && nx < MASK_W && ny < MASK_H && source[ny * MASK_W + nx]) {
                            on = 1;
                            break;
                        }
                    }
                }
                out[y * MASK_W + x] = on ? 1 : 0;
            }
        }
        return out;
    };
    const smooth = (source: Uint8Array) => {
        const out = new Uint8Array(source.length);
        for (let y = 0; y < MASK_H; y += 1) {
            for (let x = 0; x < MASK_W; x += 1) {
                let count = 0;
                for (let dy = -1; dy <= 1; dy += 1) {
                    for (let dx = -1; dx <= 1; dx += 1) {
                        const nx = x + dx;
                        const ny = y + dy;
                        if (nx >= 0 && ny >= 0 && nx < MASK_W && ny < MASK_H && source[ny * MASK_W + nx]) count += 1;
                    }
                }
                out[y * MASK_W + x] = count >= 5 ? 1 : 0;
            }
        }
        return out;
    };
    // Drop isolated specks: keep only land cells belonging to a substantial connected component.
    const keepConnected = (source: Uint8Array) => {
        const owner = new Int32Array(source.length).fill(-1);
        const sizes: number[] = [];
        const stack: number[] = [];
        for (let start = 0; start < source.length; start += 1) {
            if (!source[start] || owner[start] !== -1) continue;
            const id = sizes.length;
            let size = 0;
            stack.length = 0;
            stack.push(start);
            owner[start] = id;
            while (stack.length) {
                const cell = stack.pop()!;
                size += 1;
                const cx = cell % MASK_W;
                const cy = (cell - cx) / MASK_W;
                for (let dy = -1; dy <= 1; dy += 1) {
                    for (let dx = -1; dx <= 1; dx += 1) {
                        if (dx === 0 && dy === 0) continue;
                        const nx = cx + dx;
                        const ny = cy + dy;
                        if (nx < 0 || ny < 0 || nx >= MASK_W || ny >= MASK_H) continue;
                        const neighbor = ny * MASK_W + nx;
                        if (source[neighbor] && owner[neighbor] === -1) {
                            owner[neighbor] = id;
                            stack.push(neighbor);
                        }
                    }
                }
            }
            sizes.push(size);
        }
        const maxSize = sizes.length ? Math.max(...sizes) : 0;
        const threshold = Math.max(60, maxSize * 0.08);
        const out = new Uint8Array(source.length);
        for (let i = 0; i < source.length; i += 1) {
            if (source[i] && sizes[owner[i]] >= threshold) out[i] = 1;
        }
        return out;
    };
    let processed = dilate(mask);
    for (let y = 0; y < MASK_H; y += 1) {
        for (let x = 0; x < MASK_W; x += 1) {
            const nx = bounds.minX + (x + 0.5) * CELL_W;
            const nz = bounds.minZ + (y + 0.5) * CELL_H;
            if (Math.hypot(nx / CORE_RX, nz / CORE_RZ) > 1) processed[y * MASK_W + x] = 0;
        }
    }
    processed = smooth(processed);
    processed = smooth(processed);
    processed = keepConnected(processed);
    return processed;
}

function isLand(nx: number, nz: number): boolean {
    if (!landMask) landMask = buildLandMask();
    if (nx < bounds.minX || nx > bounds.maxX || nz < bounds.minZ || nz > bounds.maxZ) return false;
    const cx = Math.floor((nx - bounds.minX) / CELL_W);
    const cy = Math.floor((nz - bounds.minZ) / CELL_H);
    return landMask[cy * MASK_W + cx] === 1;
}

// Normalized land test for scene decoration (e.g. keeping scattered trees off the water).
export function isLandNorm(nx: number, nz: number): boolean {
    return isLand(nx, nz);
}

function pointInPolygon(nx: number, nz: number, polygon: GeoPoint[]): boolean {
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
        const [xi, zi] = polygon[i];
        const [xj, zj] = polygon[j];
        if (((zi > nz) !== (zj > nz)) && nx < ((xj - xi) * (nz - zi)) / (zj - zi) + xi) inside = !inside;
    }
    return inside;
}

function inAnyPark(nx: number, nz: number): boolean {
    return stockholmGeo.parks.some((park) => pointInPolygon(nx, nz, park));
}

// Land cells (normalized centres) for rendering the real developed-land silhouette.
export function getLandCellsNorm(): Array<{ nx: number; nz: number }> {
    if (!landMask) landMask = buildLandMask();
    const cells: Array<{ nx: number; nz: number }> = [];
    for (let y = 0; y < MASK_H; y += 1) {
        for (let x = 0; x < MASK_W; x += 1) {
            if (landMask[y * MASK_W + x]) cells.push({ nx: bounds.minX + (x + 0.5) * CELL_W, nz: bounds.minZ + (y + 0.5) * CELL_H });
        }
    }
    return cells;
}

export const landCellSizeNorm = CELL_W;

export function createCampusLayout(workspaceCount: number) {
    const count = Math.max(0, Math.floor(workspaceCount));
    const spacing = count > 48 ? 5.4 : 6.4;
    const detailLevel = getCampusDetailLevel(count);
    const ambientTarget = detailLevel === "full" ? 150 : detailLevel === "reduced" ? 60 : 0;
    let scale = 150;
    let plots: CampusPosition[] = [];
    let guard = 0;
    do {
        plots = [];
        const columns = Math.ceil((NORM_WIDTH * scale) / spacing) + 1;
        const rows = Math.ceil((NORM_DEPTH * scale) / spacing) + 1;
        for (let row = 0; row < rows; row += 1) {
            for (let column = 0; column < columns; column += 1) {
                const worldX = bounds.minX * scale + (column + 0.5) * spacing;
                const worldZ = bounds.minZ * scale + (row + 0.5) * spacing;
                const nx = worldX / scale;
                const nz = worldZ / scale;
                if (isLand(nx, nz) && !inAnyPark(nx, nz)) plots.push({ x: worldX, y: 0, z: worldZ });
            }
        }
        guard += 1;
        if (plots.length < count + ambientTarget) scale *= 1.14;
    } while (plots.length < count + ambientTarget && guard < 44);

    // Prioritise the historic centre (normalized origin ≈ Gamla Stan) so workspaces fill outward across the city.
    plots.sort((left, right) => {
        const leftDistance = Math.abs(left.x) + Math.abs(left.z) * 0.85;
        const rightDistance = Math.abs(right.x) + Math.abs(right.z) * 0.85;
        return leftDistance - rightDistance || left.z - right.z || left.x - right.x;
    });

    const worldScale = scale;
    const toWorld = (point: GeoPoint): [number, number] => [point[0] * worldScale, point[1] * worldScale];
    const inCore = (nx: number, nz: number) => Math.hypot(nx / CORE_RX, nz / CORE_RZ) < 1;
    // Drop parks/islands that float over open water (their footprint is mostly off the developed land).
    const parkOnLand = (park: GeoPoint[]) => {
        let landCount = 0;
        for (const [nx, nz] of park) if (isLand(nx, nz)) landCount += 1;
        return landCount / park.length >= 0.35;
    };
    const centroidInCore = (poly: GeoPoint[]) => {
        let cx = 0;
        let cz = 0;
        for (const [nx, nz] of poly) {
            cx += nx;
            cz += nz;
        }
        return Math.hypot(cx / poly.length / CORE_RX, cz / poly.length / CORE_RZ) < 0.98;
    };
    const keptParks = stockholmGeo.parks.filter(parkOnLand);
    const keptIslands = stockholmGeo.islands.filter(centroidInCore);
    // Keep only the road runs inside the core ellipse so nothing juts into open water at the edges.
    const clippedRoads: CampusRoad[] = [];
    for (const road of stockholmGeo.roads) {
        let run: Array<[number, number]> = [];
        for (const point of road.points) {
            if (inCore(point[0], point[1])) {
                run.push(toWorld(point));
            } else {
                if (run.length > 1) clippedRoads.push({ klass: road.klass, points: run });
                run = [];
            }
        }
        if (run.length > 1) clippedRoads.push({ klass: road.klass, points: run });
    }

    return {
        positions: plots.slice(0, count),
        ambientPositions: plots.slice(count),
        spacing,
        worldScale,
        city: { width: NORM_WIDTH * worldScale, depth: NORM_DEPTH * worldScale } as CampusBounds,
        roads: clippedRoads,
        parks: keptParks.map((park) => park.map(toWorld)),
        islands: keptIslands.map((island) => island.map(toWorld)),
        labels: districtLabels.map((label) => ({ name: label.name, x: label.x * worldScale, z: label.z * worldScale })),
        detailLevel,
        islandRadius: Math.max(15, Math.max(NORM_WIDTH, NORM_DEPTH) * 0.5 * worldScale),
    };
}

export type CampusLayout = ReturnType<typeof createCampusLayout>;
