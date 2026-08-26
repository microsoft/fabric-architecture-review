//-----------------------------------------------------------------------
// <copyright company="Microsoft Corporation">
//        Copyright (c) Microsoft Corporation.  All rights reserved.
//        Licensed under the MIT license. See LICENSE file in the project root for full license information.
// </copyright>
//-----------------------------------------------------------------------

import { useEffect, useEffectEvent, useRef, useState } from "react";
import { BadgeCheck, Box, Building2, ChevronRight, Database, FileChartColumn, Focus, GitBranch, Layers3, NotebookTabs, RotateCcw, ShieldAlert, Warehouse, X } from "lucide-react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { useReviewData } from "@/hooks/review-data.context";
import { createCampusLayout, getLandCellsNorm, isLandNorm, landCellSizeNorm, type CampusDetailLevel } from "@/lib/campus-layout";
import { type EstateHealth, type EstateItem, type EstateItemType, type TenantEstate, type WorkspaceRoom, type ReviewFinding } from "@/lib/review-data";
import { cn } from "@/lib/utils";

const itemIcon: Record<EstateItemType, typeof Database> = {
    lakehouse: Database,
    warehouse: Warehouse,
    model: Layers3,
    report: FileChartColumn,
    notebook: NotebookTabs,
    pipeline: GitBranch,
    app: Building2,
    component: Box,
};

const itemTypeOrder: EstateItemType[] = ["lakehouse", "warehouse", "pipeline", "notebook", "model", "report", "app", "component"];
const itemTypeLabel: Record<EstateItemType, string> = {
    lakehouse: "Lakehouses",
    warehouse: "Warehouses",
    pipeline: "Pipelines",
    notebook: "Notebooks",
    model: "Semantic models",
    report: "Reports",
    app: "Apps",
    component: "Other items",
};

function groupEstateItemsByType(items: EstateItem[]) {
    return itemTypeOrder
        .map((type) => ({ type, label: itemTypeLabel[type], items: items.filter((item) => item.type === type) }))
        .filter((group) => group.items.length > 0);
}

const statusColor: Record<EstateHealth, number> = {
    healthy: 0x2f8f6b,
    warning: 0xd49b32,
    risk: 0xc94f5d,
};

interface CameraMotion {
    startedAt: number;
    fromPosition: THREE.Vector3;
    fromTarget: THREE.Vector3;
    toPosition: THREE.Vector3;
    toTarget: THREE.Vector3;
}

function createLabel(text: string) {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    canvas.width = 512;
    canvas.height = 96;
    if (!context) return null;
    context.fillStyle = "rgba(16, 24, 22, 0.9)";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#ffffff";
    context.font = "600 28px Manrope, sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(text, canvas.width / 2, canvas.height / 2, canvas.width - 32);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true }));
    sprite.scale.set(4.5, 0.85, 1);
    return sprite;
}

function createDistrictLabel(text: string) {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    canvas.width = 640;
    canvas.height = 110;
    if (!context) return null;
    context.font = "700 44px Manrope, sans-serif";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.lineWidth = 8;
    context.strokeStyle = "rgba(242, 246, 241, 0.92)";
    context.strokeText(text, canvas.width / 2, canvas.height / 2, canvas.width - 30);
    context.fillStyle = "#20332f";
    context.fillText(text, canvas.width / 2, canvas.height / 2, canvas.width - 30);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false, opacity: 0.82 });
    const sprite = new THREE.Sprite(material);
    sprite.scale.set(9.6, 1.65, 1);
    sprite.renderOrder = 12;
    return sprite;
}

function addTree(scene: THREE.Scene, x: number, z: number, scale = 1) {
    const trunk = new THREE.Mesh(
        new THREE.CylinderGeometry(0.08, 0.12, 0.8, 8),
        new THREE.MeshStandardMaterial({ color: 0x7a6047 }),
    );
    trunk.position.set(x, 0.4 * scale, z);
    trunk.scale.setScalar(scale);
    trunk.castShadow = true;
    const crown = new THREE.Mesh(
        new THREE.IcosahedronGeometry(0.55, 1),
        new THREE.MeshStandardMaterial({ color: 0x4a8b65, roughness: 0.9 }),
    );
    crown.position.set(x, 1.15 * scale, z);
    crown.scale.setScalar(scale);
    crown.castShadow = true;
    scene.add(trunk, crown);
}

interface VehiclePath {
    points: Array<[number, number]>;
    cumulative: number[];
    total: number;
}

function buildPath(points: Array<[number, number]>): VehiclePath {
    const cumulative = [0];
    let total = 0;
    for (let index = 1; index < points.length; index += 1) {
        total += Math.hypot(points[index][0] - points[index - 1][0], points[index][1] - points[index - 1][1]);
        cumulative.push(total);
    }
    return { points, cumulative, total };
}

function samplePath(path: VehiclePath, distance: number) {
    const wrapped = ((distance % path.total) + path.total) % path.total;
    let index = 1;
    while (index < path.cumulative.length && path.cumulative[index] < wrapped) index += 1;
    const start = path.cumulative[index - 1];
    const segmentLength = (path.cumulative[index] ?? path.total) - start || 1;
    const t = (wrapped - start) / segmentLength;
    const [ax, az] = path.points[index - 1];
    const [bx, bz] = path.points[Math.min(index, path.points.length - 1)];
    const length = Math.hypot(bx - ax, bz - az) || 1;
    return { x: ax + (bx - ax) * t, z: az + (bz - az) * t, dirX: (bx - ax) / length, dirZ: (bz - az) / length };
}

function spawnMover(scene: THREE.Scene, mesh: THREE.Group, path: VehiclePath, startDistance: number, speed: number, laneOffset: number) {
    mesh.userData.mover = true;
    mesh.userData.path = path;
    mesh.userData.pathDistance = startDistance;
    mesh.userData.pathSpeed = speed;
    mesh.userData.laneOffset = laneOffset;
    scene.add(mesh);
}

function buildCar(color: number): THREE.Group {
    const group = new THREE.Group();
    group.position.y = 0.17;
    const body = new THREE.Mesh(new THREE.BoxGeometry(0.72, 0.2, 0.34), new THREE.MeshPhysicalMaterial({ color, metalness: 0.5, roughness: 0.25, clearcoat: 0.8 }));
    body.castShadow = true;
    const cabin = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.18, 0.3), new THREE.MeshPhysicalMaterial({ color: 0xa8c8cf, metalness: 0.15, roughness: 0.12, transparent: true, opacity: 0.82 }));
    cabin.position.set(-0.03, 0.18, 0);
    const wheelMaterial = new THREE.MeshStandardMaterial({ color: 0x171b1a, roughness: 0.9 });
    [-0.23, 0.23].forEach((wheelX) => [-0.18, 0.18].forEach((wheelZ) => {
        const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.045, 10), wheelMaterial);
        wheel.rotation.x = Math.PI / 2;
        wheel.position.set(wheelX, -0.08, wheelZ);
        group.add(wheel);
    }));
    group.add(body, cabin);
    return group;
}

function buildBus(color: number): THREE.Group {
    const group = new THREE.Group();
    group.position.y = 0.22;
    const body = new THREE.Mesh(new THREE.BoxGeometry(1.75, 0.52, 0.5), new THREE.MeshPhysicalMaterial({ color, metalness: 0.32, roughness: 0.42, clearcoat: 0.5 }));
    body.castShadow = true;
    const windows = new THREE.Mesh(new THREE.BoxGeometry(1.55, 0.2, 0.53), new THREE.MeshStandardMaterial({ color: 0x9ec2cf, metalness: 0.2, roughness: 0.14 }));
    windows.position.y = 0.12;
    const stripe = new THREE.Mesh(new THREE.BoxGeometry(1.77, 0.09, 0.53), new THREE.MeshStandardMaterial({ color: 0xf2f2ef }));
    stripe.position.y = -0.08;
    group.add(body, windows, stripe);
    const wheelMaterial = new THREE.MeshStandardMaterial({ color: 0x15181a, roughness: 0.9 });
    [-0.62, 0.62].forEach((wheelX) => [-0.28, 0.28].forEach((wheelZ) => {
        const wheel = new THREE.Mesh(new THREE.CylinderGeometry(0.14, 0.14, 0.07, 10), wheelMaterial);
        wheel.rotation.x = Math.PI / 2;
        wheel.position.set(wheelX, -0.24, wheelZ);
        group.add(wheel);
    }));
    return group;
}

function buildBike(color: number): THREE.Group {
    const group = new THREE.Group();
    group.position.y = 0.15;
    const frame = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.05, 0.05), new THREE.MeshStandardMaterial({ color, metalness: 0.4, roughness: 0.4 }));
    frame.position.y = 0.03;
    const rider = new THREE.Mesh(new THREE.CapsuleGeometry(0.055, 0.16, 3, 8), new THREE.MeshStandardMaterial({ color: 0x3a6ea5, roughness: 0.85 }));
    rider.position.set(-0.04, 0.2, 0);
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.05, 8, 6), new THREE.MeshStandardMaterial({ color: 0xc9916b }));
    head.position.set(-0.04, 0.34, 0);
    const wheelMaterial = new THREE.MeshStandardMaterial({ color: 0x1a1d1f, roughness: 0.8 });
    [-0.15, 0.15].forEach((wheelX) => {
        const wheel = new THREE.Mesh(new THREE.TorusGeometry(0.09, 0.018, 6, 12), wheelMaterial);
        wheel.position.set(wheelX, -0.04, 0);
        group.add(wheel);
    });
    group.add(frame, rider, head);
    return group;
}

function buildPerson(color: number): THREE.Group {
    const group = new THREE.Group();
    group.position.y = 0.12;
    const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.07, 0.25, 3, 8), new THREE.MeshStandardMaterial({ color, roughness: 0.86 }));
    body.position.y = 0.3;
    const head = new THREE.Mesh(new THREE.SphereGeometry(0.085, 10, 8), new THREE.MeshStandardMaterial({ color: 0xc9916b, roughness: 0.9 }));
    head.position.y = 0.59;
    group.add(body, head);
    return group;
}

function addPerson(scene: THREE.Scene, x: number, z: number, color: number) {
    const group = buildPerson(color);
    group.position.set(x, 0.12, z);
    scene.add(group);
}

// Ericsson Globe / Avicii Arena — the white paneled sphere that marks Johanneshov.
function addGloben(scene: THREE.Scene, x: number, z: number, radius: number) {
    const base = new THREE.Mesh(
        new THREE.CylinderGeometry(radius * 0.82, radius * 0.96, radius * 0.5, 28),
        new THREE.MeshStandardMaterial({ color: 0x9aa3a6, roughness: 0.82 }),
    );
    base.position.set(x, radius * 0.25, z);
    base.receiveShadow = true;
    const globe = new THREE.Mesh(
        new THREE.IcosahedronGeometry(radius, 3),
        new THREE.MeshPhysicalMaterial({ color: 0xf3f6f7, roughness: 0.36, metalness: 0.12, clearcoat: 0.55, clearcoatRoughness: 0.32, flatShading: true }),
    );
    globe.position.set(x, radius * 0.9, z);
    globe.castShadow = true;
    globe.receiveShadow = true;
    scene.add(base, globe);
}

// Stockholm City Hall (Stadshuset) — Nobel banquet venue: brick hall + tall tower crowned by three gold Tre Kronor.
function addStadshuset(scene: THREE.Scene, x: number, z: number, unit: number) {
    const group = new THREE.Group();
    group.position.set(x, 0, z);
    const brick = new THREE.MeshStandardMaterial({ color: 0x8f3f2e, roughness: 0.84, metalness: 0.04 });
    const hallW = 11 * unit;
    const hallD = 6.5 * unit;
    const hallH = 3.6 * unit;
    const hall = new THREE.Mesh(new THREE.BoxGeometry(hallW, hallH, hallD), brick);
    hall.position.y = hallH / 2 + 0.16;
    hall.castShadow = true;
    hall.receiveShadow = true;
    group.add(hall);
    const windowMat = new THREE.MeshStandardMaterial({ color: 0x2c3a3d, emissive: 0x1a2426, emissiveIntensity: 0.3, roughness: 0.3, metalness: 0.22 });
    [1, -1].forEach((side) => {
        const band = new THREE.Mesh(new THREE.BoxGeometry(hallW * 0.9, hallH * 0.5, 0.06), windowMat);
        band.position.set(0, hallH * 0.55, side * (hallD / 2 + 0.03));
        group.add(band);
    });
    const roof = new THREE.Mesh(new THREE.BoxGeometry(hallW + 0.24, 0.22 * unit, hallD + 0.24), new THREE.MeshStandardMaterial({ color: 0x3e6f63, roughness: 0.5, metalness: 0.42 }));
    roof.position.y = hallH + 0.16 + 0.11 * unit;
    group.add(roof);
    const towerW = 2.6 * unit;
    const towerH = 12 * unit;
    const towerX = hallW / 2 - towerW * 0.4;
    const towerZ = hallD * 0.1;
    const tower = new THREE.Mesh(new THREE.BoxGeometry(towerW, towerH, towerW), brick);
    tower.position.set(towerX, towerH / 2 + 0.16, towerZ);
    tower.castShadow = true;
    group.add(tower);
    const topY = towerH + 0.16;
    const lantern = new THREE.Mesh(new THREE.BoxGeometry(towerW * 0.72, towerW * 0.9, towerW * 0.72), new THREE.MeshStandardMaterial({ color: 0x9a7b52, roughness: 0.5, metalness: 0.42 }));
    lantern.position.set(towerX, topY + towerW * 0.45, towerZ);
    group.add(lantern);
    const spire = new THREE.Mesh(new THREE.ConeGeometry(towerW * 0.34, towerW * 2.2, 8), new THREE.MeshStandardMaterial({ color: 0x3c4b4a, roughness: 0.5, metalness: 0.5 }));
    spire.position.set(towerX, topY + towerW * 2.0, towerZ);
    group.add(spire);
    const gold = new THREE.MeshStandardMaterial({ color: 0xd9b64a, emissive: 0x5a4416, emissiveIntensity: 0.45, roughness: 0.3, metalness: 0.92 });
    [-0.55, 0, 0.55].forEach((offset, index) => {
        const crown = new THREE.Mesh(new THREE.SphereGeometry(towerW * 0.15, 12, 10), gold);
        crown.position.set(towerX + offset * towerW * 0.55, topY + towerW * (index === 1 ? 1.5 : 1.28), towerZ);
        group.add(crown);
    });
    scene.add(group);
}

// Small Scandinavian parish church — pale plaster nave with a spired tower and a gilded cross.
function addChurch(scene: THREE.Scene, x: number, z: number, unit: number) {
    const group = new THREE.Group();
    group.position.set(x, 0, z);
    const wall = new THREE.MeshStandardMaterial({ color: 0xe9e3d6, roughness: 0.84 });
    const roofMat = new THREE.MeshStandardMaterial({ color: 0x46554f, roughness: 0.58, metalness: 0.32 });
    const naveW = 1.5 * unit;
    const naveD = 2.8 * unit;
    const naveH = 1.5 * unit;
    const nave = new THREE.Mesh(new THREE.BoxGeometry(naveW, naveH, naveD), wall);
    nave.position.set(0, naveH / 2 + 0.16, 0.6 * unit);
    nave.castShadow = true;
    group.add(nave);
    const naveRoof = new THREE.Mesh(new THREE.BoxGeometry(naveW + 0.12, 0.5 * unit, naveD + 0.12), roofMat);
    naveRoof.position.set(0, naveH + 0.16 + 0.25 * unit, 0.6 * unit);
    naveRoof.castShadow = true;
    group.add(naveRoof);
    const towerW = 1.0 * unit;
    const towerH = 3.3 * unit;
    const tower = new THREE.Mesh(new THREE.BoxGeometry(towerW, towerH, towerW), wall);
    tower.position.set(0, towerH / 2 + 0.16, -1.0 * unit);
    tower.castShadow = true;
    group.add(tower);
    const spire = new THREE.Mesh(new THREE.ConeGeometry(towerW * 0.64, 1.7 * unit, 4), roofMat);
    spire.position.set(0, towerH + 0.16 + 0.85 * unit, -1.0 * unit);
    spire.rotation.y = Math.PI / 4;
    spire.castShadow = true;
    group.add(spire);
    const gilt = new THREE.MeshStandardMaterial({ color: 0xe8d9a0, metalness: 0.7, roughness: 0.3 });
    const crossV = new THREE.Mesh(new THREE.BoxGeometry(0.05 * unit, 0.5 * unit, 0.05 * unit), gilt);
    crossV.position.set(0, towerH + 0.16 + 1.85 * unit, -1.0 * unit);
    group.add(crossV);
    const crossH = new THREE.Mesh(new THREE.BoxGeometry(0.28 * unit, 0.05 * unit, 0.05 * unit), gilt);
    crossH.position.set(0, crossV.position.y + 0.08 * unit, -1.0 * unit);
    group.add(crossH);
    scene.add(group);
}

function addAmbientBuilding(scene: THREE.Scene, position: { x: number; z: number }, index: number, spacing: number, historic = false) {
    const group = new THREE.Group();
    group.position.set(position.x, 0, position.z);
    group.rotation.y = ((index % 4) - 1.5) * 0.08;
    // Stockholm is predominantly warm, pitched-roof stone; keep modern flat blocks a minority.
    const warmStyle = historic || index % 4 !== 0;
    const height = warmStyle ? 1.5 + (index % 4) * 0.34 : 2.4 + ((index * 7) % 9) * 0.5;
    const width = spacing * (0.54 + (index % 3) * 0.05);
    const depth = spacing * (0.5 + ((index + 1) % 3) * 0.045);
    const baseY = 0.16;
    const wallColors = warmStyle
        ? [0x8b3a2f, 0xb5773a, 0xcaa14e, 0xe4cf94, 0xe8dcc0, 0x9aa06f, 0xc0714a, 0xa8894f]
        : [0xb7bcb6, 0x9aa6a4, 0xc4c2b6, 0x879494, 0xa9b0aa];
    const wall = new THREE.MeshStandardMaterial({ color: wallColors[index % wallColors.length], roughness: 0.76, metalness: 0.05 });
    const body = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), wall);
    body.position.y = baseY + height / 2;
    body.castShadow = true;
    body.receiveShadow = true;
    group.add(body);

    const windowMaterial = new THREE.MeshStandardMaterial({ color: 0x9ac0c8, emissive: 0x27363b, emissiveIntensity: 0.32, roughness: 0.24, metalness: 0.34 });
    [1, -1].forEach((side) => {
        const facade = new THREE.Mesh(new THREE.BoxGeometry(width * 0.78, height * 0.78, 0.04), windowMaterial);
        facade.position.set(0, baseY + height / 2, side * (depth / 2 + 0.02));
        group.add(facade);
    });
    const sideFacade = new THREE.Mesh(new THREE.BoxGeometry(0.04, height * 0.78, depth * 0.72), windowMaterial);
    sideFacade.position.set((index % 2 === 0 ? 1 : -1) * (width / 2 + 0.02), baseY + height / 2, 0);
    group.add(sideFacade);

    if (warmStyle) {
        const roofColors = [0x6a3b31, 0x2f3438, 0x3e5a55, 0x7a4436, 0x4a5a52, 0x5c4a3a];
        const roofMaterial = new THREE.MeshStandardMaterial({ color: roofColors[index % roofColors.length], roughness: 0.62, metalness: 0.22 });
        const roofHeight = height * 0.42;
        const roof = new THREE.Mesh(new THREE.ConeGeometry(Math.max(width, depth) * 0.72, roofHeight, 4), roofMaterial);
        roof.position.y = baseY + height + roofHeight / 2;
        roof.rotation.y = Math.PI / 4;
        roof.castShadow = true;
        group.add(roof);
        if (index % 2 === 0) {
            const chimney = new THREE.Mesh(new THREE.BoxGeometry(0.14, 0.5, 0.14), new THREE.MeshStandardMaterial({ color: 0x7d6f63, roughness: 0.9 }));
            chimney.position.set(width * 0.22, baseY + height + roofHeight * 0.4, depth * 0.16);
            group.add(chimney);
        }
    } else {
        const parapet = new THREE.Mesh(
            new THREE.BoxGeometry(width + 0.08, 0.18, depth + 0.08),
            new THREE.MeshStandardMaterial({ color: 0x6b726e, roughness: 0.82, metalness: 0.16 }),
        );
        parapet.position.y = baseY + height + 0.03;
        parapet.castShadow = true;
        group.add(parapet);
        const unit = new THREE.Mesh(new THREE.BoxGeometry(width * 0.42, 0.32, depth * 0.36), new THREE.MeshStandardMaterial({ color: 0x565d5a, roughness: 0.8 }));
        unit.position.set(-width * 0.14, baseY + height + 0.26, -depth * 0.1);
        group.add(unit);
        if (index % 3 === 0) {
            const antenna = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 0.75, 6), new THREE.MeshStandardMaterial({ color: 0x3a403d }));
            antenna.position.set(width * 0.2, baseY + height + 0.5, depth * 0.12);
            group.add(antenna);
        }
    }
    scene.add(group);
}

function addLandmarkCrown(group: THREE.Group, style: number, width: number, depth: number, roofY: number, roomId: string) {
    const stone = new THREE.MeshStandardMaterial({ color: 0xc8c0ae, roughness: 0.46, metalness: 0.22 });
    const steel = new THREE.MeshStandardMaterial({ color: 0x8ba6aa, roughness: 0.24, metalness: 0.72 });
    if (style === 0) {
        [[0.72, 1.2], [0.48, 0.9], [0.28, 0.65]].forEach(([scale, height], tier) => {
            const crown = new THREE.Mesh(new THREE.BoxGeometry(width * scale, height, depth * scale), tier === 2 ? steel : stone);
            crown.position.y = roofY + 0.6 + tier * 0.88;
            crown.userData.roomId = roomId;
            group.add(crown);
        });
        const spire = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.1, 2.6, 10), steel);
        spire.position.y = roofY + 4.15;
        group.add(spire);
    } else if (style === 1) {
        const crown = new THREE.Mesh(new THREE.ConeGeometry(width * 0.48, 2.4, 8), steel);
        crown.position.y = roofY + 1.25;
        crown.userData.roomId = roomId;
        group.add(crown);
        const needle = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.07, 1.7, 8), steel);
        needle.position.y = roofY + 3.15;
        group.add(needle);
    } else if (style === 2) {
        const taper = new THREE.Mesh(new THREE.ConeGeometry(width * 0.6, 2.8, 4), new THREE.MeshPhysicalMaterial({ color: 0x77a3ac, roughness: 0.16, metalness: 0.34, clearcoat: 0.5 }));
        taper.position.y = roofY + 1.45;
        taper.rotation.y = Math.PI / 4;
        taper.userData.roomId = roomId;
        group.add(taper);
        const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.09, 1.5, 8), steel);
        mast.position.y = roofY + 3.55;
        group.add(mast);
    } else {
        const wedge = new THREE.Mesh(new THREE.CylinderGeometry(width * 0.5, width * 0.58, 1.8, 3), stone);
        wedge.position.y = roofY + 0.92;
        wedge.scale.z = 0.58;
        wedge.rotation.y = Math.PI / 2;
        wedge.userData.roomId = roomId;
        group.add(wedge);
    }
}

function createBuilding(room: WorkspaceRoom, position: THREE.Vector3, index: number, detailLevel: CampusDetailLevel, showLabel: boolean) {
    const group = new THREE.Group();
    group.position.copy(position);
    group.userData.roomId = room.id;
    const width = 3.85 + (index % 3) * 0.18;
    const depth = 3.05 + (index % 2) * 0.24;
    const itemGroups = groupEstateItemsByType(room.items);
    const floorHeight = Math.min(1.55, 1.05 + Math.min(room.items.length, 22) * 0.035);
    const hasFacadeDetail = detailLevel !== "minimal";
    const hasFullDetail = detailLevel === "full";
    const workspaceColor = statusColor[room.status];
    const totalHeight = 0.32 + itemGroups.length * floorHeight;
    if (detailLevel === "minimal") {
        const towerHeight = Math.max(1.3, totalHeight);
        const tower = new THREE.Mesh(
            new THREE.BoxGeometry(width, towerHeight, depth),
            new THREE.MeshStandardMaterial({ color: 0x8ba4a3, emissive: workspaceColor, emissiveIntensity: 0.18, metalness: 0.32, roughness: 0.46 }),
        );
        tower.position.y = towerHeight / 2 + 0.12;
        tower.userData.roomId = room.id;
        group.add(tower);
        return group;
    }
    const podium = new THREE.Mesh(new THREE.BoxGeometry(width + 1.25, 0.22, depth + 1.25), new THREE.MeshStandardMaterial({ color: 0xe5efeb, emissive: workspaceColor, emissiveIntensity: 0.16, roughness: 0.68 }));
    podium.position.y = 0.11;
    podium.receiveShadow = true;
    podium.userData.roomId = room.id;
    group.add(podium);

    const plazaRing = new THREE.Mesh(
        new THREE.RingGeometry(Math.max(width, depth) * 0.72, Math.max(width, depth) * 0.82, 32),
        new THREE.MeshStandardMaterial({ color: workspaceColor, emissive: workspaceColor, emissiveIntensity: 0.5, side: THREE.DoubleSide }),
    );
    plazaRing.rotation.x = -Math.PI / 2;
    plazaRing.position.y = 0.235;
    plazaRing.userData.roomId = room.id;
    group.add(plazaRing);

    if (hasFacadeDetail) {
        const entryDeck = new THREE.Mesh(new THREE.BoxGeometry(width * 0.58, 0.12, 1.25), new THREE.MeshStandardMaterial({ color: 0x506b63, roughness: 0.68 }));
        entryDeck.position.set(0.35, 0.22, depth / 2 + 0.72);
        entryDeck.castShadow = true;
        group.add(entryDeck);
    }

    const structureMaterial = new THREE.MeshStandardMaterial({ color: index % 4 === 0 ? 0xd8d3c8 : 0xcbd4d2, metalness: 0.58, roughness: 0.34 });
    const darkMetal = new THREE.MeshStandardMaterial({ color: index % 3 === 0 ? 0x243944 : 0x2f4844, metalness: 0.72, roughness: 0.28 });
    if (hasFacadeDetail) {
        [-1, 1].forEach((xSide) => [-1, 1].forEach((zSide) => {
            const column = new THREE.Mesh(new THREE.BoxGeometry(0.12, totalHeight, 0.12), structureMaterial);
            column.position.set(xSide * (width / 2 + 0.12), totalHeight / 2 + 0.18, zSide * (depth / 2 + 0.12));
            column.castShadow = hasFullDetail;
            group.add(column);
        }));
        [-1, 1].forEach((side) => {
            const locatorFrame = new THREE.Mesh(
                new THREE.BoxGeometry(0.1, totalHeight + 0.55, 0.1),
                new THREE.MeshStandardMaterial({ color: workspaceColor, emissive: workspaceColor, emissiveIntensity: 0.55 }),
            );
            locatorFrame.position.set(side * (width / 2 + 0.23), totalHeight / 2 + 0.22, depth / 2 + 0.24);
            locatorFrame.userData.roomId = room.id;
            group.add(locatorFrame);
        });
    }

    const core = new THREE.Mesh(new THREE.BoxGeometry(0.68, totalHeight + 0.35, depth * 0.48), darkMetal);
    core.position.set(width / 2 + 0.48, totalHeight / 2 + 0.18, -0.34);
    core.castShadow = true;
    group.add(core);

    itemGroups.forEach((itemGroup, index) => {
        const groupStatus = itemGroup.items.some((item) => item.status === "risk") ? "risk" : itemGroup.items.some((item) => item.status === "warning") ? "warning" : "healthy";
        const floor = new THREE.Mesh(new THREE.BoxGeometry(width, floorHeight - 0.14, depth), new THREE.MeshPhysicalMaterial({ color: index % 2 === 0 ? 0x7899a1 : 0x8db2ae, metalness: 0.2, roughness: 0.18, transmission: 0.08, transparent: true, opacity: 0.92, clearcoat: 0.55 }));
        floor.position.y = 0.28 + floorHeight * index + floorHeight / 2;
        floor.castShadow = hasFullDetail;
        floor.receiveShadow = true;
        floor.userData.roomId = room.id;
        floor.userData.itemType = itemGroup.type;
        floor.userData.floorSurface = true;
        group.add(floor);

        const slab = new THREE.Mesh(new THREE.BoxGeometry(width + 0.2, 0.08, depth + 0.2), structureMaterial);
        slab.position.y = floor.position.y - floorHeight / 2 + 0.04;
        group.add(slab);

        if (hasFacadeDetail) {
            const glass = new THREE.Mesh(
                new THREE.BoxGeometry(width - 0.38, floorHeight * 0.52, 0.08),
                new THREE.MeshPhysicalMaterial({ color: 0x79a9a2, transparent: true, opacity: 0.54, metalness: 0.25, roughness: 0.18 }),
            );
            glass.position.set(0, floor.position.y, depth / 2 + 0.05);
            glass.userData.roomId = room.id;
            glass.userData.itemType = itemGroup.type;
            group.add(glass);
        }

        const signal = new THREE.Mesh(
            new THREE.BoxGeometry(0.18, floorHeight * 0.52, 0.08),
            new THREE.MeshStandardMaterial({ color: statusColor[groupStatus], emissive: statusColor[groupStatus], emissiveIntensity: 0.22 }),
        );
        signal.position.set(-width / 2 + 0.16, floor.position.y, depth / 2 + 0.07);
        signal.userData.roomId = room.id;
        signal.userData.itemType = itemGroup.type;
        group.add(signal);

        if (hasFullDetail) {
            Array.from({ length: 7 }, (_, finIndex) => {
                const fin = new THREE.Mesh(new THREE.BoxGeometry(0.035, floorHeight * 0.72, 0.32), structureMaterial);
                fin.position.set(-width / 2 + 0.42 + finIndex * ((width - 0.84) / 6), floor.position.y, depth / 2 + 0.2);
                group.add(fin);
            });
        }
    });

    const roof = new THREE.Mesh(new THREE.BoxGeometry(width + 0.45, 0.11, depth + 0.45), darkMetal);
    roof.position.y = totalHeight;
    roof.castShadow = true;
    roof.userData.roomId = room.id;
    group.add(roof);

    if (hasFullDetail) addLandmarkCrown(group, index % 4, width, depth, roof.position.y, room.id);

    if (hasFullDetail) {
        const canopy = new THREE.Mesh(new THREE.BoxGeometry(width + 1.1, 0.08, depth * 0.68), new THREE.MeshPhysicalMaterial({ color: 0xd6e4e1, metalness: 0.35, roughness: 0.18, transparent: true, opacity: 0.88 }));
        canopy.position.set(-0.28, roof.position.y + 0.35, 0.16);
        canopy.castShadow = true;
        group.add(canopy);
        [-1, 1].forEach((side) => {
            const mast = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.7, 8), structureMaterial);
            mast.position.set(side * width * 0.36, roof.position.y + 0.18, 0.16);
            group.add(mast);
        });
    }

    const beacon = new THREE.Mesh(
        new THREE.CylinderGeometry(0.16, 0.16, 0.28, 16),
        new THREE.MeshStandardMaterial({ color: statusColor[room.status], emissive: statusColor[room.status], emissiveIntensity: 0.65 }),
    );
    beacon.position.y = roof.position.y + 0.22;
    beacon.userData.beacon = true;
    beacon.userData.roomId = room.id;
    group.add(beacon);

    const locator = new THREE.Mesh(
        new THREE.OctahedronGeometry(0.25, 0),
        new THREE.MeshStandardMaterial({ color: workspaceColor, emissive: workspaceColor, emissiveIntensity: 1.15 }),
    );
    locator.position.y = roof.position.y + (hasFullDetail ? 4.9 : 1.05);
    locator.userData.beacon = true;
    locator.userData.roomId = room.id;
    group.add(locator);

    const label = hasFullDetail && showLabel ? createLabel(room.name) : null;
    if (label) {
        label.position.set(0, roof.position.y + 5.15, 0);
        label.userData.roomId = room.id;
        label.userData.buildingLabel = true;
        group.add(label);
    }
    return group;
}

interface CampusSelection {
    roomId: string;
    itemId?: string;
}

export type EstateReviewArea = "governance" | "models" | "efficiency" | "architecture" | "notebooks";

export interface EstateReviewContext {
    workspaceId: string;
    itemId?: string;
}

function CampusCanvas({ compact, estate, selectedRoomId, selectedItemId, onSelect }: { compact: boolean; estate: TenantEstate; selectedRoomId: string | null; selectedItemId: string | null; onSelect: (selection: CampusSelection) => void }) {
    const hostRef = useRef<HTMLDivElement>(null);
    const focusSelectionRef = useRef<(roomId: string | null, itemId: string | null) => void>(() => undefined);
    const onSelectEvent = useEffectEvent(onSelect);

    useEffect(() => {
        focusSelectionRef.current(selectedRoomId, selectedItemId);
    }, [selectedRoomId, selectedItemId]);

    useEffect(() => {
        const host = hostRef.current;
        if (!host) return;
        const showFallback = () => {
            const fallback = host.querySelector<HTMLElement>(".campus-webgl-fallback");
            if (fallback) fallback.hidden = false;
        };
        if (navigator.userAgent.includes("jsdom")) {
            showFallback();
            return;
        }
        const scene = new THREE.Scene();
        scene.background = new THREE.Color(0xb7d3df);
        const layout = createCampusLayout(estate.rooms.length);
        const cityExtent = layout.islandRadius;
        scene.fog = new THREE.Fog(0xb7d3df, cityExtent * 2.5, cityExtent * 5.5);
        const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
        camera.far = Math.max(120, cityExtent * 6);
        camera.position.set(layout.city.width * 0.08, cityExtent * 2.15, layout.city.depth * 1.18);
        let renderer: THREE.WebGLRenderer;
        try {
            renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
        } catch {
            showFallback();
            return;
        }
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, layout.detailLevel === "full" ? 2 : 1.35));
        renderer.shadowMap.enabled = layout.detailLevel !== "minimal";
        renderer.shadowMap.type = THREE.PCFShadowMap;
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        renderer.toneMapping = THREE.ACESFilmicToneMapping;
        renderer.toneMappingExposure = 1.05;
        renderer.domElement.setAttribute("aria-label", "Interactive 3D FAR Estate map. Drag to orbit and scroll to zoom.");
        renderer.domElement.setAttribute("role", "img");
        host.appendChild(renderer.domElement);

        const controls = new OrbitControls(camera, renderer.domElement);
        controls.enableDamping = true;
        controls.dampingFactor = 0.07;
        controls.minDistance = 5;
        controls.maxDistance = Math.max(42, cityExtent * 4);
        controls.maxPolarAngle = Math.PI * 0.47;
        controls.target.set(layout.city.width * 0.04, 1.2, layout.city.depth * 0.04);

        scene.add(new THREE.HemisphereLight(0xeef7f4, 0x52645f, 2.3));
        const sun = new THREE.DirectionalLight(0xffffff, 3.4);
        sun.position.set(-10, 18, 12);
        sun.castShadow = layout.detailLevel !== "minimal";
        sun.shadow.mapSize.set(layout.detailLevel === "full" ? 2048 : 1024, layout.detailLevel === "full" ? 2048 : 1024);
        sun.shadow.camera.left = -cityExtent;
        sun.shadow.camera.right = cityExtent;
        sun.shadow.camera.top = cityExtent;
        sun.shadow.camera.bottom = -cityExtent;
        scene.add(sun);

        const water = new THREE.Mesh(
            new THREE.BoxGeometry(layout.city.width * 3, 0.32, layout.city.depth * 3),
            new THREE.MeshPhysicalMaterial({ color: 0x3f7d90, roughness: 0.2, metalness: 0.08, clearcoat: 0.7, clearcoatRoughness: 0.16 }),
        );
        water.position.y = -0.34;
        water.receiveShadow = true;
        scene.add(water);
        // Proper earcut triangulation (via ShapeGeometry) so concave parks/islands render as clean flat patches, not glitchy fans.
        const capGeometry = (points: Array<[number, number]>) => {
            const shape = new THREE.Shape();
            shape.moveTo(points[0][0], points[0][1]);
            for (let i = 1; i < points.length; i += 1) shape.lineTo(points[i][0], points[i][1]);
            shape.closePath();
            const geometry = new THREE.ShapeGeometry(shape);
            geometry.rotateX(Math.PI / 2);
            return geometry;
        };
        const pointInPoly = (x: number, z: number, poly: Array<[number, number]>) => {
            let inside = false;
            for (let i = 0, j = poly.length - 1; i < poly.length; j = i, i += 1) {
                const [xi, zi] = poly[i];
                const [xj, zj] = poly[j];
                if (((zi > z) !== (zj > z)) && x < ((xj - xi) * (z - zi)) / (zj - zi) + xi) inside = !inside;
            }
            return inside;
        };
        // Real developed-land silhouette rasterized from the OpenStreetMap street network.
        const landCells = getLandCellsNorm();
        const cellWorld = landCellSizeNorm * layout.worldScale;
        const landMesh = new THREE.InstancedMesh(
            new THREE.BoxGeometry(cellWorld * 1.45, 0.5, cellWorld * 1.45),
            new THREE.MeshStandardMaterial({ color: 0x9ba08d, roughness: 0.97 }),
            landCells.length,
        );
        landMesh.receiveShadow = true;
        const landDummy = new THREE.Object3D();
        landCells.forEach((cell, index) => {
            landDummy.position.set(cell.nx * layout.worldScale, -0.19, cell.nz * layout.worldScale);
            landDummy.updateMatrix();
            landMesh.setMatrixAt(index, landDummy.matrix);
        });
        landMesh.instanceMatrix.needsUpdate = true;
        scene.add(landMesh);
        layout.islands.forEach((island) => {
            const patch = new THREE.Mesh(capGeometry(island), new THREE.MeshStandardMaterial({ color: 0x8f9a72, roughness: 0.96, side: THREE.DoubleSide }));
            patch.position.y = 0.11;
            patch.receiveShadow = true;
            scene.add(patch);
        });
        layout.parks.forEach((park) => {
            const green = new THREE.Mesh(capGeometry(park), new THREE.MeshStandardMaterial({ color: 0x5f9a5f, roughness: 0.95, side: THREE.DoubleSide }));
            green.position.y = 0.15;
            green.receiveShadow = true;
            scene.add(green);
        });
        if (layout.detailLevel !== "minimal" && host.clientWidth >= 640) {
            layout.labels.forEach((label) => {
                const sprite = createDistrictLabel(label.name);
                if (sprite) {
                    sprite.position.set(label.x, 6.5, label.z);
                    scene.add(sprite);
                }
            });
        }
        // Footprints where landmarks stand, so ambient buildings/sidewalks don't overlap them.
        const landmarkKeepouts: Array<{ x: number; z: number; r: number }> = [];
        if (layout.detailLevel !== "minimal") {
            const district = (name: string) => layout.labels.find((label) => label.name === name);
            // Nudge a target point to the nearest developed land so landmarks never sit on water.
            const snapToLand = (wx: number, wz: number) => {
                if (isLandNorm(wx / layout.worldScale, wz / layout.worldScale)) return { wx, wz };
                for (let radius = layout.worldScale * 0.01; radius <= layout.worldScale * 0.28; radius += layout.worldScale * 0.01) {
                    for (let step = 0; step < 32; step += 1) {
                        const angle = (step / 32) * Math.PI * 2;
                        const tx = wx + Math.cos(angle) * radius;
                        const tz = wz + Math.sin(angle) * radius;
                        if (isLandNorm(tx / layout.worldScale, tz / layout.worldScale)) return { wx: tx, wz: tz };
                    }
                }
                return { wx, wz };
            };
            const globen = district("JOHANNESHOV");
            if (globen) {
                addGloben(scene, globen.x, globen.z, 4);
                landmarkKeepouts.push({ x: globen.x, z: globen.z, r: 7 });
            }
            const cityHall = district("KUNGSHOLMEN");
            const gamlaStan = district("GAMLA STAN");
            if (cityHall && gamlaStan) {
                const spot = snapToLand(cityHall.x + (gamlaStan.x - cityHall.x) * 0.4, cityHall.z + (gamlaStan.z - cityHall.z) * 0.4);
                addStadshuset(scene, spot.wx, spot.wz, 0.7);
                landmarkKeepouts.push({ x: spot.wx, z: spot.wz, r: 9 });
            }
            const churchSpots: Array<{ name: string; dx: number; dz: number }> = [
                { name: "NORRMALM", dx: -6, dz: 4 },
                { name: "VASASTAN", dx: 5, dz: -3 },
                { name: "ÖSTERMALM", dx: -4, dz: 5 },
                { name: "SÖDERMALM", dx: 7, dz: 3 },
                { name: "GAMLA STAN", dx: 3, dz: -5 },
                { name: "KUNGSHOLMEN", dx: 12, dz: -6 },
            ];
            churchSpots.forEach((spot, index) => {
                const anchor = district(spot.name);
                if (!anchor) return;
                const churchX = anchor.x + spot.dx;
                const churchZ = anchor.z + spot.dz;
                if (!isLandNorm(churchX / layout.worldScale, churchZ / layout.worldScale)) return;
                addChurch(scene, churchX, churchZ, 0.85 + (index % 2) * 0.2);
                landmarkKeepouts.push({ x: churchX, z: churchZ, r: 3.5 });
            });
        }

        const roadPaths: VehiclePath[] = [];
        if (layout.detailLevel !== "minimal") {
            const roadY = 0.12;
            const asphaltVerts: number[] = [];
            const asphaltIdx: number[] = [];
            const paveVerts: number[] = [];
            const paveIdx: number[] = [];
            const pushQuad = (verts: number[], idx: number[], ax: number, az: number, bx: number, bz: number, perpX: number, perpZ: number, half: number, y: number) => {
                const base = verts.length / 3;
                verts.push(ax + perpX * half, y, az + perpZ * half, bx + perpX * half, y, bz + perpZ * half, bx - perpX * half, y, bz - perpZ * half, ax - perpX * half, y, az - perpZ * half);
                idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
            };
            layout.roads.forEach((road) => {
                const roadWidth = road.klass >= 3 ? 2.2 : road.klass === 2 ? 1.6 : 1.15;
                for (let i = 1; i < road.points.length; i += 1) {
                    const [ax, az] = road.points[i - 1];
                    const [bx, bz] = road.points[i];
                    const length = Math.hypot(bx - ax, bz - az) || 1;
                    const perpX = -(bz - az) / length;
                    const perpZ = (bx - ax) / length;
                    pushQuad(asphaltVerts, asphaltIdx, ax, az, bx, bz, perpX, perpZ, roadWidth / 2, roadY);
                    const paveOffset = roadWidth / 2 + 0.28;
                    pushQuad(paveVerts, paveIdx, ax + perpX * paveOffset, az + perpZ * paveOffset, bx + perpX * paveOffset, bz + perpZ * paveOffset, perpX, perpZ, 0.28, roadY + 0.02);
                    pushQuad(paveVerts, paveIdx, ax - perpX * paveOffset, az - perpZ * paveOffset, bx - perpX * paveOffset, bz - perpZ * paveOffset, perpX, perpZ, 0.28, roadY + 0.02);
                }
                if (road.klass >= 3 && road.points.length > 1) roadPaths.push(buildPath(road.points));
            });
            const mergedMesh = (verts: number[], idx: number[], color: number) => {
                const geometry = new THREE.BufferGeometry();
                geometry.setAttribute("position", new THREE.Float32BufferAttribute(verts, 3));
                geometry.setIndex(idx);
                geometry.computeVertexNormals();
                const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color, roughness: 0.9, side: THREE.DoubleSide }));
                mesh.receiveShadow = true;
                return mesh;
            };
            scene.add(mergedMesh(paveVerts, paveIdx, 0xbcb3a1));
            scene.add(mergedMesh(asphaltVerts, asphaltIdx, 0x34393b));
        }
        const ambientCap = layout.detailLevel === "full" ? 460 : 260;
        const renderedAmbient = layout.detailLevel === "minimal" ? [] : layout.ambientPositions.slice(0, ambientCap);
        if (layout.detailLevel !== "minimal") {
            const inKeepout = (x: number, z: number) => landmarkKeepouts.some((keepout) => Math.hypot(x - keepout.x, z - keepout.z) < keepout.r);
            const ambientPlots = renderedAmbient.filter((plot) => !inKeepout(plot.x, plot.z));
            const sidewalkMaterial = new THREE.MeshStandardMaterial({ color: 0xb7b1a2, roughness: 0.94 });
            [...layout.positions, ...ambientPlots].forEach((plot) => {
                const sidewalk = new THREE.Mesh(new THREE.BoxGeometry(4.4, 0.14, 3.9), sidewalkMaterial);
                sidewalk.position.set(plot.x, 0.06, plot.z);
                sidewalk.receiveShadow = true;
                scene.add(sidewalk);
            });
            const centre = layout.worldScale * 0.1;
            ambientPlots.forEach((position, index) => {
                addAmbientBuilding(scene, position, index, layout.spacing, Math.hypot(position.x, position.z) < centre);
            });
            let treeBudget = layout.detailLevel === "full" ? 460 : 180;
            layout.parks.forEach((park) => {
                if (treeBudget <= 0) return;
                let minX = Infinity;
                let maxX = -Infinity;
                let minZ = Infinity;
                let maxZ = -Infinity;
                for (const [x, z] of park) {
                    minX = Math.min(minX, x);
                    maxX = Math.max(maxX, x);
                    minZ = Math.min(minZ, z);
                    maxZ = Math.max(maxZ, z);
                }
                const step = Math.max(3.2, (maxX - minX) / 9);
                for (let z = minZ; z <= maxZ && treeBudget > 0; z += step) {
                    for (let x = minX; x <= maxX && treeBudget > 0; x += step) {
                        const jitterX = x + Math.sin(x * 12.9 + z * 7.3) * step * 0.3;
                        const jitterZ = z + Math.cos(x * 4.1 + z * 9.7) * step * 0.3;
                        if (!pointInPoly(jitterX, jitterZ, park)) continue;
                        addTree(scene, jitterX, jitterZ, 0.7 + (treeBudget % 3) * 0.12);
                        treeBudget -= 1;
                    }
                }
            });
            // Djurgården is Stockholm's green royal park island — give it a dense grove.
            const djurgarden = layout.labels.find((label) => label.name === "DJURGÅRDEN");
            if (djurgarden) {
                const groveCount = layout.detailLevel === "full" ? 74 : 32;
                for (let i = 0; i < groveCount; i += 1) {
                    const angle = i * 2.399963;
                    const radius = Math.sqrt((i + 1) / groveCount) * layout.worldScale * 0.15;
                    const treeX = djurgarden.x + Math.cos(angle) * radius;
                    const treeZ = djurgarden.z + Math.sin(angle) * radius;
                    if (!isLandNorm(treeX / layout.worldScale, treeZ / layout.worldScale)) continue;
                    addTree(scene, treeX, treeZ, 0.68 + (i % 3) * 0.12);
                }
            }
        }
        if (layout.detailLevel !== "minimal" && roadPaths.length) {
            const carColors = [0xf2bd36, 0xc94f5d, 0xe8ece9, 0x3d7182, 0x52605c, 0xd98a3d, 0xbcc2bf];
            const carCount = layout.detailLevel === "full" ? 22 : 10;
            Array.from({ length: carCount }, (_, index) => {
                const path = roadPaths[index % roadPaths.length];
                const car = buildCar(carColors[index % carColors.length]);
                spawnMover(scene, car, path, (index * 3.7) % path.total, 0.006 + (index % 3) * 0.0016, index % 2 === 0 ? 0.34 : -0.34);
            });
            const busColors = [0xd23b3b, 0x2f6fae];
            const busCount = layout.detailLevel === "full" ? 5 : 2;
            Array.from({ length: busCount }, (_, index) => {
                const path = roadPaths[(index * 2 + 1) % roadPaths.length];
                const bus = buildBus(busColors[index % busColors.length]);
                spawnMover(scene, bus, path, (index * 6.4) % path.total, 0.0045, 0.34);
            });
            const bikeColors = [0x2f6f65, 0xd98a3d, 0x8a5a9e, 0x3a6ea5];
            const bikeCount = layout.detailLevel === "full" ? 16 : 6;
            Array.from({ length: bikeCount }, (_, index) => {
                const path = roadPaths[index % roadPaths.length];
                const bike = buildBike(bikeColors[index % bikeColors.length]);
                spawnMover(scene, bike, path, (index * 2.9) % path.total, 0.0035 + (index % 2) * 0.001, index % 2 === 0 ? 0.66 : -0.66);
            });
            const walkerColors = [0xc94f5d, 0x2f6f65, 0xe3a33b, 0x405b86, 0x7e5b49];
            const walkerCount = layout.detailLevel === "full" ? 28 : 12;
            Array.from({ length: walkerCount }, (_, index) => {
                const path = roadPaths[index % roadPaths.length];
                const person = buildPerson(walkerColors[index % walkerColors.length]);
                spawnMover(scene, person, path, (index * 4.3) % path.total, 0.0011 + (index % 3) * 0.0004, index % 2 === 0 ? 0.98 : -0.98);
            });
        }
        if (layout.detailLevel !== "minimal") {
            const personColors = [0xc94f5d, 0x2f6f65, 0xe3a33b, 0x405b86, 0x7e5b49];
            layout.parks.slice(0, 8).forEach((park, parkIndex) => {
                let cx = 0;
                let cz = 0;
                for (const [x, z] of park) {
                    cx += x;
                    cz += z;
                }
                cx /= park.length;
                cz /= park.length;
                for (let i = 0; i < 5; i += 1) {
                    addPerson(scene, cx + (i - 2) * 2.2, cz + (i % 2 === 0 ? -1.8 : 1.8), personColors[(parkIndex + i) % personColors.length]);
                }
            });
        }

        const buildings = estate.rooms.map((room, index) => {
            const plot = layout.positions[index];
            const building = createBuilding(room, new THREE.Vector3(plot.x, plot.y + 0.18, plot.z), index, layout.detailLevel, host.clientWidth >= 640);
            scene.add(building);
            return building;
        });

        const raycaster = new THREE.Raycaster();
        const pointer = new THREE.Vector2();
        const pointerStart = new THREE.Vector2();
        let frameId = 0;
        let cameraMotion: CameraMotion | null = null;
        const homePosition = new THREE.Vector3(layout.city.width * 0.08, cityExtent * 2.15, layout.city.depth * 1.18);
        const homeTarget = new THREE.Vector3(layout.city.width * 0.04, 1.2, layout.city.depth * 0.04);
        let isFocused = false;
        const focusSelection = (roomId: string | null, itemId: string | null) => {
            isFocused = Boolean(roomId);
            const building = buildings.find((candidate) => candidate.userData.roomId === roomId);
            scene.traverse((object) => {
                if (object.userData.buildingLabel) object.visible = roomId === null || object.userData.roomId === roomId;
                if (object.userData.floorSurface) {
                    const selected = Boolean(itemId && object.userData.itemId === itemId);
                    object.scale.set(selected ? 1.035 : 1, selected ? 1.06 : 1, selected ? 1.035 : 1);
                    if (object instanceof THREE.Mesh && object.material instanceof THREE.MeshPhysicalMaterial) {
                        object.material.emissive.setHex(selected ? 0x2f8f6b : 0x000000);
                        object.material.emissiveIntensity = selected ? 0.34 : 0;
                    }
                }
            });
            const selectedFloor = building?.children.find((child) => child.userData.itemId === itemId && child.userData.floorSurface);
            const floorHeight = selectedFloor?.position.y ?? 1.4;
            const toPosition = building ? building.position.clone().add(new THREE.Vector3(6.8, floorHeight + 4.3, 8)) : homePosition;
            const toTarget = building ? building.position.clone().add(new THREE.Vector3(0, floorHeight, 0)) : homeTarget;
            cameraMotion = {
                startedAt: performance.now(),
                fromPosition: camera.position.clone(),
                fromTarget: controls.target.clone(),
                toPosition,
                toTarget,
            };
        };
        focusSelectionRef.current = focusSelection;
        const onPointerDown = (event: PointerEvent) => pointerStart.set(event.clientX, event.clientY);
        const onPointerUp = (event: PointerEvent) => {
            if (pointerStart.distanceTo(new THREE.Vector2(event.clientX, event.clientY)) > 5) return;
            const bounds = renderer.domElement.getBoundingClientRect();
            pointer.set(((event.clientX - bounds.left) / bounds.width) * 2 - 1, -((event.clientY - bounds.top) / bounds.height) * 2 + 1);
            raycaster.setFromCamera(pointer, camera);
            const hit = raycaster.intersectObjects(buildings, true)[0];
            const roomId = hit?.object.userData.roomId as string | undefined;
            const itemId = hit?.object.userData.itemId as string | undefined;
            if (roomId) {
                onSelectEvent({ roomId, itemId });
            }
        };
        renderer.domElement.addEventListener("pointerdown", onPointerDown);
        renderer.domElement.addEventListener("pointerup", onPointerUp);

        const resize = () => {
            const width = host.clientWidth;
            const height = host.clientHeight;
            const aspect = width / Math.max(height, 1);
            camera.aspect = aspect;
            camera.updateProjectionMatrix();
            renderer.setSize(width, height, false);
            if (!isFocused && !cameraMotion) {
                const portrait = aspect < 0.72;
                homePosition.set(
                    layout.city.width * (portrait ? 0.16 : 0.08),
                    cityExtent * (portrait ? 2.8 : 2.15),
                    layout.city.depth * (portrait ? 1.18 : 1.18),
                );
                homeTarget.set(layout.city.width * 0.04, portrait ? 0.7 : 1.2, layout.city.depth * 0.04);
                camera.position.copy(homePosition);
                controls.target.copy(homeTarget);
            }
        };
        const resizeObserver = new ResizeObserver(resize);
        resizeObserver.observe(host);
        resize();

        const beacons: THREE.Object3D[] = [];
        const movers: THREE.Object3D[] = [];
        scene.traverse((object) => {
            if (object.userData.beacon) beacons.push(object);
            if (object.userData.mover) movers.push(object);
        });
        let previousFrameTime = performance.now();
        const animate = (time: number) => {
            frameId = requestAnimationFrame(animate);
            const delta = Math.min(40, time - previousFrameTime);
            previousFrameTime = time;
            if (cameraMotion) {
                const progress = Math.min((time - cameraMotion.startedAt) / 850, 1);
                const eased = 1 - Math.pow(1 - progress, 3);
                camera.position.lerpVectors(cameraMotion.fromPosition, cameraMotion.toPosition, eased);
                controls.target.lerpVectors(cameraMotion.fromTarget, cameraMotion.toTarget, eased);
                if (progress === 1) cameraMotion = null;
            }
            beacons.forEach((object) => {
                object.scale.y = 0.9 + Math.sin(time * 0.004) * 0.18;
            });
            movers.forEach((object) => {
                const data = object.userData as { path: VehiclePath; pathDistance: number; pathSpeed: number; laneOffset: number };
                data.pathDistance += data.pathSpeed * delta;
                const point = samplePath(data.path, data.pathDistance);
                object.position.x = point.x - point.dirZ * data.laneOffset;
                object.position.z = point.z + point.dirX * data.laneOffset;
                object.rotation.y = -Math.atan2(point.dirZ, point.dirX);
            });
            controls.update();
            renderer.render(scene, camera);
        };
        frameId = requestAnimationFrame(animate);

        return () => {
            cancelAnimationFrame(frameId);
            resizeObserver.disconnect();
            renderer.domElement.removeEventListener("pointerdown", onPointerDown);
            renderer.domElement.removeEventListener("pointerup", onPointerUp);
            controls.dispose();
            scene.traverse((object) => {
                if (object instanceof THREE.Mesh) {
                    object.geometry.dispose();
                    const materials = Array.isArray(object.material) ? object.material : [object.material];
                    materials.forEach((material) => material.dispose());
                }
                if (object instanceof THREE.Sprite && object.material.map) object.material.map.dispose();
            });
            renderer.dispose();
            host.removeChild(renderer.domElement);
            focusSelectionRef.current = () => undefined;
        };
    }, [compact, estate]);

    return <div className="campus-canvas" ref={hostRef}><div className="campus-webgl-fallback" hidden><Building2 className="icon-size-500" /><span>3D rendering is unavailable. Select a workspace below to inspect it.</span></div></div>;
}

function ArtifactProfile({ item }: { item: EstateItem }) {
    return (
        <div className="mt-400 border-t border-border pt-400">
            <div className="mb-300 flex items-center gap-200"><BadgeCheck className="icon-size-200 text-primary-strong" /><p className="text-100 font-bold uppercase text-muted-foreground">Artifact governance</p></div>
            <dl className="grid grid-cols-2 gap-200 text-200">
                <div><dt className="text-muted-foreground">Endorsement</dt><dd className="font-semibold capitalize">{item.governance?.endorsement ?? "Not assessed"}</dd></div>
                <div><dt className="text-muted-foreground">Sensitivity</dt><dd className="font-semibold">{item.governance?.sensitivityLabel ?? "Not assessed"}</dd></div>
                <div className="col-span-2"><dt className="text-muted-foreground">Owner</dt><dd className="font-semibold">{item.governance?.owner ?? "Not recorded"}</dd></div>
            </dl>
            {item.modelProfile && <dl className="mt-300 grid grid-cols-3 gap-200 border-t border-border pt-300 text-200">
                <div className="col-span-2"><dt className="text-muted-foreground">Storage</dt><dd className="font-semibold">{item.modelProfile.storageMode}</dd></div>
                <div><dt className="text-muted-foreground">Size</dt><dd className="font-semibold">{item.modelProfile.totalSize}</dd></div>
                <div><dt className="text-muted-foreground">Tables</dt><dd className="font-numeric font-semibold">{item.modelProfile.tables}</dd></div>
                <div><dt className="text-muted-foreground">Columns</dt><dd className="font-numeric font-semibold">{item.modelProfile.columns}</dd></div>
                <div><dt className="text-muted-foreground">Calculated</dt><dd className="font-numeric font-semibold">{item.modelProfile.calculatedColumns}</dd></div>
            </dl>}
        </div>
    );
}

function WorkspaceInspector({ findings: allFindings, room, selectedItemId, onSelectItem, onOpenArea, onClose }: { findings: ReviewFinding[]; room: WorkspaceRoom; selectedItemId: string | null; onSelectItem: (itemId: string) => void; onOpenArea?: (area: EstateReviewArea, context: EstateReviewContext) => void; onClose: () => void }) {
    const selectedItem = room.items.find((item) => item.id === selectedItemId) ?? null;
    const linkedFindingIds = selectedItem ? selectedItem.findingIds : room.findingIds;
    const findings = allFindings.filter((finding) => linkedFindingIds.includes(finding.id));
    return (
        <aside className="campus-inspector" aria-label={`${room.name} workspace inspector`}>
            <div className="flex items-start justify-between gap-300 border-b border-border p-400">
                <div>
                    <p className="section-kicker">{room.domain}</p>
                    <h3 className="font-heading text-500 font-bold">{room.name}</h3>
                    <p className="mt-100 text-200 text-muted-foreground">{room.items.length} Fabric items · Risk {room.riskScore}/100</p>
                </div>
                <button className="icon-button" aria-label="Close workspace inspector" onClick={onClose} type="button"><X className="icon-size-200" /></button>
            </div>
            <div className="p-400">
                <p className="mb-300 text-100 font-bold uppercase text-muted-foreground">Floors by item type</p>
                <div className="space-y-300">
                    {groupEstateItemsByType(room.items).map((group, floorIndex) => {
                        const Icon = itemIcon[group.type];
                        return <section key={group.type} aria-label={`${group.label} floor`}>
                            <div className="flex items-center gap-200 bg-secondary px-200 py-200 text-100 font-bold uppercase text-muted-foreground"><span className="font-numeric">{String(floorIndex + 1).padStart(2, "0")}</span><Icon className="icon-size-200 text-primary-strong" /><span>{group.label}</span><span className="ml-auto font-numeric">{group.items.length}</span></div>
                            {group.items.map((item) => <button className={cn("flex w-full items-center gap-300 border-b border-border px-300 py-200 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", selectedItem?.id === item.id && "bg-primary-soft text-primary-strong")} key={item.id} onClick={() => onSelectItem(item.id)} aria-label={`${group.label} · Floor ${String(floorIndex + 1).padStart(2, "0")} · ${item.name}`} type="button"><span className="min-w-0 flex-1 truncate text-200 font-semibold">{item.name}</span><span className={cn("icon-size-100 rounded-full", item.status === "risk" ? "bg-destructive" : item.status === "warning" ? "bg-warning" : "bg-success")} aria-label={item.status} /></button>)}
                        </section>;
                    })}
                </div>
                {selectedItem && <ArtifactProfile item={selectedItem} />}
                {onOpenArea && <div className="mt-400 border-t border-border pt-400">
                    <p className="mb-200 text-100 font-bold uppercase text-muted-foreground">Open specialist analysis</p>
                    <div className="grid gap-100">
                        {selectedItem?.type === "model" && <button className="flex items-center gap-200 px-200 py-200 text-left text-200 font-semibold text-primary-strong hover:bg-primary-soft" onClick={() => onOpenArea("models", { workspaceId: room.id, itemId: selectedItem.id })} type="button"><Layers3 className="icon-size-200" />Open semantic model optimization<ChevronRight className="ml-auto icon-size-200" /></button>}
                        {selectedItem?.type === "notebook" && <button className="flex items-center gap-200 px-200 py-200 text-left text-200 font-semibold text-primary-strong hover:bg-primary-soft" onClick={() => onOpenArea("notebooks", { workspaceId: room.id, itemId: selectedItem.id })} type="button"><NotebookTabs className="icon-size-200" />Open notebook engineering<ChevronRight className="ml-auto icon-size-200" /></button>}
                        <button className="flex items-center gap-200 px-200 py-200 text-left text-200 font-semibold text-primary-strong hover:bg-primary-soft" onClick={() => onOpenArea("governance", { workspaceId: room.id, itemId: selectedItem?.id })} type="button"><BadgeCheck className="icon-size-200" />Open governance<ChevronRight className="ml-auto icon-size-200" /></button>
                        <button className="flex items-center gap-200 px-200 py-200 text-left text-200 font-semibold text-primary-strong hover:bg-primary-soft" onClick={() => onOpenArea("architecture", { workspaceId: room.id, itemId: selectedItem?.id })} type="button"><GitBranch className="icon-size-200" />Open architecture<ChevronRight className="ml-auto icon-size-200" /></button>
                        <button className="flex items-center gap-200 px-200 py-200 text-left text-200 font-semibold text-primary-strong hover:bg-primary-soft" onClick={() => onOpenArea("efficiency", { workspaceId: room.id, itemId: selectedItem?.id })} type="button"><Database className="icon-size-200" />Open performance + cost<ChevronRight className="ml-auto icon-size-200" /></button>
                    </div>
                </div>}
                <div className="mt-400 border-t border-border pt-400">
                    <div className="mb-300 flex items-center gap-200"><ShieldAlert className="icon-size-200 text-destructive" /><p className="text-100 font-bold uppercase text-muted-foreground">{selectedItem ? "Artifact flags" : "Workspace flags"}</p></div>
                    {findings.length ? findings.map((finding) => <article className="border-l-2 border-destructive pl-300" key={finding.id}><p className="font-monospace text-100 font-bold text-destructive">{finding.id} · {finding.severity}</p><h4 className="mt-100 text-200 font-bold">{finding.title}</h4><p className="mt-100 text-200 text-muted-foreground">{finding.recommendation}</p></article>) : <p className="text-200 text-muted-foreground">No high-impact finding is linked to this sample workspace.</p>}
                </div>
            </div>
        </aside>
    );
}

export function EstateMap({ compact = false, onOpenArea }: { compact?: boolean; onOpenArea?: (area: EstateReviewArea, context: EstateReviewContext) => void }) {
    const { estate, findings } = useReviewData();
    const [selectedRoomId, setSelectedRoomId] = useState<string | null>(null);
    const [selectedItemId, setSelectedItemId] = useState<string | null>(null);
    const [capacityFilter, setCapacityFilter] = useState("all");
    const capacities = [...new Set(estate.rooms.map((room) => room.capacityName ?? room.domain ?? "Unassigned capacity"))]
        .sort((left, right) => left.localeCompare(right));
    const filteredRooms = capacityFilter === "all"
        ? estate.rooms
        : estate.rooms.filter((room) => (room.capacityName ?? room.domain ?? "Unassigned capacity") === capacityFilter);
    const filteredEstate = { ...estate, rooms: filteredRooms };
    const selectedRoom = filteredRooms.find((room) => room.id === selectedRoomId);
    const selectRoom = ({ roomId, itemId }: CampusSelection) => {
        setSelectedRoomId(roomId);
        setSelectedItemId(itemId ?? null);
    };
    const resetCampus = () => {
        setSelectedRoomId(null);
        setSelectedItemId(null);
    };
    const filterCapacity = (capacityName: string) => {
        setCapacityFilter(capacityName);
        resetCampus();
    };
    return (
        <section className={cn("campus-explorer", compact && "campus-explorer-compact")} aria-label="Interactive 3D Stockholm FAR Estate map">
            <CampusCanvas compact={compact} estate={filteredEstate} onSelect={selectRoom} selectedItemId={selectedItemId} selectedRoomId={selectedRoomId} />
            <div className="campus-toolbar"><span className="campus-live"><span /> Stockholm · FAR Estate map</span><span className="hidden text-100 text-muted-foreground sm:inline">Drag to orbit · Scroll to zoom · Select a building or floor</span>{!compact && capacities.length > 1 && <label className="pointer-events-auto flex items-center gap-200 bg-card px-300 py-200 text-100 font-bold shadow-lg"><span className="hidden sm:inline">Capacity</span><select aria-label="Filter estate by capacity" className="max-w-56 bg-transparent font-semibold text-foreground outline-none" value={capacityFilter} onChange={(event) => filterCapacity(event.target.value)}><option value="all">All capacities ({estate.rooms.length})</option>{capacities.map((capacity) => <option key={capacity} value={capacity}>{capacity} ({estate.rooms.filter((room) => (room.capacityName ?? room.domain ?? "Unassigned capacity") === capacity).length})</option>)}</select></label>}{!compact && selectedRoomId && <button className="pointer-events-auto inline-flex items-center gap-200 bg-card px-300 py-200 text-100 font-bold shadow-lg" onClick={resetCampus} type="button"><RotateCcw className="icon-size-100" />View full city</button>}</div>
            {!compact && <details className="pointer-events-auto absolute left-400 top-1200 z-20 max-w-64 border border-border bg-card/95 text-100 shadow-lg backdrop-blur-md"><summary className="cursor-pointer px-300 py-200 font-semibold text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">Map controls</summary><p className="border-t border-border px-300 py-200 leading-200 text-muted-foreground">Drag or swipe to orbit. Scroll or pinch to zoom. Keyboard users can Tab through the workspace buttons below the map and press Enter to inspect one.</p></details>}
            <div className="campus-selector" aria-label="Workspace buildings">
                {filteredRooms.length > 60
                    ? <select aria-label="Select workspace building" className="min-w-64 bg-card px-300 py-200 text-200 font-semibold text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring" value={selectedRoomId ?? ""} onChange={(event) => event.target.value ? selectRoom({ roomId: event.target.value }) : resetCampus()}><option value="">Select a workspace</option>{filteredRooms.map((room) => <option key={room.id} value={room.id}>{room.name} · {room.status}</option>)}</select>
                    : filteredRooms.map((room) => <button className={cn("campus-building-button", selectedRoomId === room.id && "campus-building-button-active")} key={room.id} onClick={() => selectRoom({ roomId: room.id })} type="button"><Building2 className="icon-size-200" /><span>{room.name}</span><span className={cn("icon-size-100 rounded-full", room.status === "risk" ? "bg-destructive" : room.status === "warning" ? "bg-warning" : "bg-success")} /></button>)}
            </div>
            {!compact && selectedRoom && <WorkspaceInspector findings={findings} room={selectedRoom} selectedItemId={selectedItemId} onSelectItem={(itemId) => selectRoom({ roomId: selectedRoom.id, itemId })} onOpenArea={onOpenArea} onClose={resetCampus} />}
            {!compact && !selectedRoom && <div className="campus-empty-state"><Focus className="icon-size-300" /><span>Select a building to inspect its Fabric items and findings</span></div>}
            <div className="campus-identity"><Box className="icon-size-200" /><span>{estate.name}</span></div>
        </section>
    );
}
