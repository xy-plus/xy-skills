function featureGroupPlan() {
  const composite = Object.keys(features).some((featureId) => featureId.includes("/"));
  const groups = [];
  const groupByKey = new Map();
  const groupByFeature = new Map();
  if (composite) {
    const local = { key: "local", colorIndex: 1, childIds: [], leafCount: 0 };
    groups.push(local);
    groupByKey.set(local.key, local);
  }
  for (const childId of rootFeature.children) {
    const key = composite
      ? childId.includes("/") ? `repository:${childId}` : "local"
      : `branch:${childId}`;
    let group = groupByKey.get(key);
    if (!group) {
      group = { key, colorIndex: groups.length + 1, childIds: [], leafCount: 0 };
      groups.push(group);
      groupByKey.set(key, group);
    }
    group.childIds.push(childId);
  }

  function assignGroup(featureId, group) {
    groupByFeature.set(featureId, group);
    for (const childId of features[featureId].children) assignGroup(childId, group);
  }
  for (const group of groups) {
    for (const childId of group.childIds) assignGroup(childId, group);
  }

  const leafCounts = new Map();
  function countLeaves(featureId) {
    if (leafCounts.has(featureId)) return leafCounts.get(featureId);
    const children = features[featureId].children;
    const count = children.length
      ? children.reduce((sum, childId) => sum + countLeaves(childId), 0)
      : 1;
    leafCounts.set(featureId, count);
    return count;
  }
  for (const group of groups) {
    group.leafCount = group.childIds.reduce((sum, childId) => sum + countLeaves(childId), 0);
    group.color = group.colorIndex <= 8
      ? `var(--branch-${group.colorIndex})`
      : "var(--muted)";
  }
  return { composite, groups, groupByFeature };
}

function choosePictureSides(groups) {
  const active = groups.filter((group) => group.childIds.length);
  if (active.length < 2) return { left: [], right: active };
  const total = active.reduce((sum, group) => sum + group.leafCount, 0);
  let leftLeaves = 0;
  let split = 1;
  let bestDifference = Infinity;
  for (let index = 1; index < active.length; index++) {
    leftLeaves += active[index - 1].leafCount;
    const difference = Math.abs(total - 2 * leftLeaves);
    if (difference < bestDifference) {
      bestDifference = difference;
      split = index;
    }
  }
  return { left: active.slice(0, split), right: active.slice(split) };
}

function pictureNodeStyle(featureId, level, composite, context) {
  const feature = features[featureId];
  const isRoot = featureId === rootId;
  const isRepositoryRoot = composite && feature.parent === rootId && featureId.includes("/");
  const isLeaf = feature.children.length === 0;
  const fontSize = isRoot ? 15 : isRepositoryRoot ? 19 : isLeaf ? 13 : level === 1 ? 15 : 13.5;
  const fontWeight = isRoot || isRepositoryRoot ? 700 : isLeaf ? 400 : 600;
  const label = isRoot || isRepositoryRoot ? feature.directory_name : feature.title;
  const color = isRoot
    ? composite ? "var(--branch-1)" : "var(--ink)"
    : context.groupByFeature.get(featureId).color;
  return { isRoot, isRepositoryRoot, isLeaf, fontSize, fontWeight, label, color };
}

function breakPictureLabel(value, context, style, maxWidth, maxLines) {
  const pieces = value.match(/[A-Za-z0-9]+(?:[_-][A-Za-z0-9]+)*|\s+|./gu) || [value];
  const lines = [];
  let line = "";
  context.font = `${style.fontWeight} ${style.fontSize}px ${style.fontFamily}`;
  while (pieces.length) {
    const piece = pieces.shift();
    if (!line || context.measureText(line + piece).width <= maxWidth) {
      line += piece;
      continue;
    }
    let breakAt = -1;
    for (let index = 0; index < line.length; index++) {
      if ("、，：）".includes(line[index]) || /\s/.test(line[index])) breakAt = index;
    }
    if (breakAt >= 0) {
      lines.push(line.slice(0, breakAt + 1).trim());
      line = line.slice(breakAt + 1).trim();
    } else {
      lines.push(line.trim());
      line = "";
    }
    if (lines.length >= maxLines - 1) {
      line = (line + piece + pieces.join("")).trim();
      pieces.length = 0;
    } else {
      pieces.unshift(piece);
    }
  }
  if (line || !lines.length) lines.push(line.trim());
  return lines;
}

function pictureRadius(featureId, maximumLines) {
  if (featureId === rootId) return rootPictureRadius;
  const lineCount = sumLines(features[featureId].lines.total);
  return maximumLines ? 4 + 18 * Math.sqrt(lineCount / maximumLines) : 4;
}

function pictureNodeInfo(featureId, level, context, measure, maximumLines) {
  const feature = features[featureId];
  const style = pictureNodeStyle(featureId, level, context.composite, context);
  style.fontFamily = context.fontFamily;
  const radius = pictureRadius(featureId, maximumLines);
  const isRoot = featureId === rootId;
  let lines;
  if (isRoot) {
    lines = pictureRootLines(style.label);
  } else if (style.isLeaf) {
    lines = [style.label];
  } else {
    lines = breakPictureLabel(style.label, measure, style, 210, 3);
  }
  measure.font = `${style.fontWeight} ${style.fontSize}px ${style.fontFamily}`;
  const labelWidth = Math.max(0, ...lines.map((line) => measure.measureText(line).width));
  const lineHeight = style.fontSize * 1.2;
  let top;
  let bottom;
  if (isRoot) {
    top = -radius;
    bottom = radius;
  } else if (style.isLeaf) {
    top = Math.min(-radius, -style.fontSize * 0.8 - 3);
    bottom = Math.max(radius, style.fontSize * 0.6 + 3);
  } else {
    top = Math.min(
      -radius,
      -radius - 8 - (lines.length - 1) * lineHeight - style.fontSize * 0.8 - 3,
    );
    bottom = Math.max(radius, -radius - 8 + style.fontSize * 0.25 + 3);
  }
  const labelInset = style.isLeaf ? 0 : 10 + labelWidth + 3;
  const labelOutset = style.isLeaf ? 10 + labelWidth + 3 : 0;
  return {
    featureId,
    level,
    feature,
    ...style,
    lines,
    lineHeight,
    radius,
    labelWidth,
    inwardExtent: radius + labelInset,
    outwardExtent: radius + labelOutset,
    top,
    bottom,
  };
}

function buildPictureSubtree(featureId, level, infoByFeature) {
  const info = infoByFeature.get(featureId);
  const childIds = info.feature.children;
  const own = [{ featureId, level, y: 0, info }];
  if (!childIds.length) return { nodes: own, top: info.top, bottom: info.bottom, info };

  const children = childIds.map((childId) =>
    buildPictureSubtree(childId, level + 1, infoByFeature));
  const centers = [0];
  for (let index = 1; index < children.length; index++) {
    const previous = children[index - 1];
    const current = children[index];
    const baseGap = Math.max(
      25,
      previous.info.radius + current.info.radius + 9,
    );
    const distance = Math.max(
      baseGap,
      previous.bottom - current.top + 10,
    );
    centers.push(centers[index - 1] + distance);
  }
  const parentY = (centers[0] + centers[centers.length - 1]) / 2;
  let top = info.top;
  let bottom = info.bottom;
  const nodes = [...own];
  for (let index = 0; index < children.length; index++) {
    const offset = centers[index] - parentY;
    const child = children[index];
    top = Math.min(top, child.top + offset);
    bottom = Math.max(bottom, child.bottom + offset);
    for (const node of child.nodes) nodes.push({ ...node, y: node.y + offset });
  }
  return { nodes, top, bottom, info };
}

function combinePictureSide(childIds, side, infoByFeature, groupByFeature) {
  const branches = childIds.map((childId) =>
    buildPictureSubtree(childId, 1, infoByFeature));
  if (!branches.length) return { nodes: [], top: 0, bottom: 0 };
  const centers = [0];
  for (let index = 1; index < branches.length; index++) {
    const previous = branches[index - 1];
    const current = branches[index];
    const previousGroup = groupByFeature.get(childIds[index - 1]);
    const currentGroup = groupByFeature.get(childIds[index]);
    const firstBranchGap = 18;
    const groupGap = previousGroup.key === currentGroup.key ? 0 : 28;
    const parentGap = 12;
    const baseGap = Math.max(
      25,
      previous.info.radius + current.info.radius + 9,
    ) + firstBranchGap + groupGap + parentGap;
    const distance = Math.max(
      baseGap,
      previous.bottom - current.top + firstBranchGap + groupGap + parentGap,
    );
    centers.push(centers[index - 1] + distance);
  }
  let top = Infinity;
  let bottom = -Infinity;
  for (let index = 0; index < branches.length; index++) {
    top = Math.min(top, branches[index].top + centers[index]);
    bottom = Math.max(bottom, branches[index].bottom + centers[index]);
  }
  const shift = -(top + bottom) / 2;
  const nodes = [];
  for (let index = 0; index < branches.length; index++) {
    for (const node of branches[index].nodes) {
      nodes.push({ ...node, y: node.y + centers[index] + shift, side });
    }
  }
  return { nodes, top: top + shift, bottom: bottom + shift };
}

function pictureRootLines(name) {
  return name.match(/[^-]+-?/g) || [name];
}

function addPictureText(node, lines, x, baselines, info, className, anchor) {
  const svgNamespace = "http://www.w3.org/2000/svg";
  const label = document.createElementNS(svgNamespace, "text");
  label.setAttribute("class", className);
  label.setAttribute("text-anchor", anchor);
  label.setAttribute("font-size", String(info.fontSize));
  label.setAttribute("font-weight", String(info.fontWeight));
  label.setAttribute("font-family", "var(--sans)");
  for (let index = 0; index < lines.length; index++) {
    const span = document.createElementNS(svgNamespace, "tspan");
    span.setAttribute("x", String(x));
    span.setAttribute("y", String(baselines[index]));
    span.textContent = lines[index];
    label.append(span);
  }
  node.append(label);
}

function renderPicture() {
  const svgNamespace = "http://www.w3.org/2000/svg";
  const context = featureGroupPlan();
  const split = choosePictureSides(context.groups);
  const leftKeys = new Set(split.left.map((group) => group.key));
  const rightKeys = new Set(split.right.map((group) => group.key));
  const leftIds = rootFeature.children.filter((childId) =>
    leftKeys.has(context.groupByFeature.get(childId)?.key));
  const rightIds = rootFeature.children.filter((childId) =>
    rightKeys.has(context.groupByFeature.get(childId)?.key));
  const maximumLines = Math.max(
    0,
    ...Object.entries(features)
      .filter(([featureId]) => featureId !== rootId)
      .map(([, feature]) => sumLines(feature.lines.total)),
  );
  const measureCanvas = document.createElement("canvas");
  const measure = measureCanvas.getContext("2d");
  const fontFamily = getComputedStyle(document.body).fontFamily;
  const infoByFeature = new Map();
  context.fontFamily = fontFamily;
  for (const featureId of Object.keys(features)) {
    const level = featureId === rootId ? 0 : featureLevel(featureId);
    infoByFeature.set(
      featureId,
      pictureNodeInfo(featureId, level, context, measure, maximumLines),
    );
  }

  const left = combinePictureSide(leftIds, -1, infoByFeature, context.groupByFeature);
  const right = combinePictureSide(rightIds, 1, infoByFeature, context.groupByFeature);
  const maximumDepthOnSide = Math.max(
    0,
    ...[...left.nodes, ...right.nodes].map((node) => node.level),
  );
  const levelExtents = new Map();
  for (const node of [...left.nodes, ...right.nodes]) {
    const extent = levelExtents.get(node.level) || { inward: 0, outward: 0 };
    extent.inward = Math.max(extent.inward, node.info.inwardExtent);
    extent.outward = Math.max(extent.outward, node.info.outwardExtent);
    levelExtents.set(node.level, extent);
  }
  const distanceByLevel = new Map();
  let distance = 0;
  const rootRadius = rootPictureRadius;
  for (let level = 1; level <= maximumDepthOnSide; level++) {
    const extent = levelExtents.get(level) || { inward: 0, outward: 0 };
    if (level === 1) distance = rootRadius + extent.inward + 34;
    else {
      const previous = levelExtents.get(level - 1) || { outward: 0 };
      distance += previous.outward + extent.inward + 34;
    }
    distanceByLevel.set(level, distance);
  }
  const maximumSideExtent = Math.max(
    rootRadius + 36,
    ...[...levelExtents.entries()].map(([level, extent]) =>
      distanceByLevel.get(level) + extent.outward + 36),
  );
  const width = Math.ceil(maximumSideExtent * 2);
  const rootX = width / 2;
  const verticalExtent = Math.max(
    rootRadius,
    Math.abs(left.top), Math.abs(left.bottom), Math.abs(right.top), Math.abs(right.bottom),
  );
  const height = Math.ceil((verticalExtent + 42) * 2);
  const centerY = height / 2;
  const rootPictureInfo = infoByFeature.get(rootId);
  const nodePositions = new Map();
  nodePositions.set(rootId, {
    featureId: rootId,
    x: rootX,
    y: centerY,
    side: 1,
    info: rootPictureInfo,
  });
  for (const node of [...left.nodes, ...right.nodes]) {
    const x = rootX + node.side * distanceByLevel.get(node.level);
    const positioned = {
      ...node,
      x,
      y: centerY + node.y,
      parentId: node.info.feature.parent,
    };
    nodePositions.set(node.featureId, positioned);
  }

  const svg = document.querySelector("#picture-svg");
  svg.replaceChildren();
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", String(width));
  svg.setAttribute("height", String(height));
  const definitions = document.createElementNS(svgNamespace, "defs");
  const marker = document.createElementNS(svgNamespace, "marker");
  marker.setAttribute("id", "picture-arrow");
  marker.setAttribute("viewBox", "0 0 10 10");
  marker.setAttribute("refX", "8");
  marker.setAttribute("refY", "5");
  marker.setAttribute("markerWidth", "6");
  marker.setAttribute("markerHeight", "6");
  marker.setAttribute("orient", "auto");
  marker.setAttribute("markerUnits", "strokeWidth");
  const arrow = document.createElementNS(svgNamespace, "path");
  arrow.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
  arrow.setAttribute("fill", "context-stroke");
  arrow.setAttribute("opacity", "0.5");
  marker.append(arrow);
  definitions.append(marker);
  svg.append(definitions);

  for (const [featureId, node] of nodePositions) {
    if (featureId === rootId) continue;
    const parent = nodePositions.get(node.parentId);
    const side = node.side;
    const startX = parent.x + side * parent.info.radius;
    const endX = node.x - side * node.info.radius;
    const deltaX = endX - startX;
    const edge = document.createElementNS(svgNamespace, "path");
    edge.setAttribute("class", "tree-edge");
    edge.setAttribute(
      "d",
      `M ${startX} ${parent.y} C ${startX + deltaX * 0.45} ${parent.y}, ${endX - deltaX * 0.45} ${node.y}, ${endX} ${node.y}`,
    );
    edge.setAttribute("marker-end", "url(#picture-arrow)");
    edge.style.setProperty("--tree-color", node.info.color);
    svg.append(edge);
  }

  for (const [featureId, position] of nodePositions) {
    const info = position.info;
    const node = document.createElementNS(svgNamespace, "g");
    node.setAttribute("class", `picture-node${featureId === rootId ? " root-node" : ""}`);
    node.setAttribute("role", "button");
    node.setAttribute("tabindex", "0");
    node.setAttribute("aria-label", featureName(features[featureId]));
    node.setAttribute("data-feature-id", featureId);
    node.setAttribute("transform", `translate(${position.x} ${position.y})`);
    if (selectedFeatureId === featureId) node.classList.add("selected");
    const color = featureId === rootId
      ? context.composite ? "var(--branch-1)" : "var(--ink)"
      : info.color;
    node.style.setProperty("--node-color", color);

    const hitArea = document.createElementNS(svgNamespace, "rect");
    const horizontalLabel = info.labelWidth + 10;
    let hitLeft;
    let hitRight;
    if (featureId === rootId) {
      hitLeft = -info.radius;
      hitRight = info.radius;
    } else if (info.isLeaf) {
      hitLeft = position.side > 0 ? -info.radius : -info.radius - horizontalLabel;
      hitRight = position.side > 0 ? info.radius + horizontalLabel : info.radius;
    } else {
      hitLeft = position.side > 0 ? -info.radius - horizontalLabel : -info.radius;
      hitRight = position.side > 0 ? info.radius : info.radius + horizontalLabel;
    }
    hitArea.setAttribute("class", "picture-hit-area");
    hitArea.setAttribute("x", String(hitLeft - 3));
    hitArea.setAttribute("y", String(info.top - 3));
    hitArea.setAttribute("width", String(hitRight - hitLeft + 6));
    hitArea.setAttribute("height", String(info.bottom - info.top + 6));
    node.append(hitArea);

    const circle = document.createElementNS(svgNamespace, "circle");
    circle.setAttribute("r", String(info.radius));
    node.append(circle);
    if (featureId === rootId) {
      const rootLines = pictureRootLines(rootFeature.directory_name);
      const lineHeight = 17;
      const firstBaseline = -((rootLines.length - 1) * lineHeight) / 2 + 5;
      const rootLabelInfo = { fontSize: 15, fontWeight: 700 };
      node.style.setProperty(
        "--root-label-color",
        context.composite ? "#fff" : "var(--surface)",
      );
      addPictureText(
        node,
        rootLines,
        0,
        rootLines.map((_, index) => firstBaseline + index * lineHeight),
        rootLabelInfo,
        "picture-root-label",
        "middle",
      );
    } else {
      const isInternal = !info.isLeaf;
      const labelX = position.side * (isInternal ? -1 : 1) * (info.radius + 10);
      const anchorDirection = position.side * (isInternal ? -1 : 1);
      const anchor = anchorDirection > 0 ? "start" : "end";
      const firstBaseline = isInternal
        ? -info.radius - 8 - (info.lines.length - 1) * info.lineHeight
        : info.fontSize * 0.35;
      addPictureText(
        node,
        info.lines,
        labelX,
        info.lines.map((_, index) => firstBaseline + index * info.lineHeight),
        info,
        `picture-label${info.isLeaf ? " leaf-label" : ""}`,
        anchor,
      );
    }
    node.addEventListener("click", () => openDetails(featureId));
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDetails(featureId);
      }
    });
    pictureNodes.set(featureId, node);
    svg.append(node);
  }

  const note = context.composite
    ? "箭头从上级指向子功能；圆越大行数越多（含子功能）；同色同仓"
    : "箭头从上级指向子功能；圆越大行数越多（含子功能）；同色同顶层分支";
  document.querySelector("#picture-note").textContent = note;
  pictureLayout = { width, height };
  refreshPictureScale(false);
}

function updatePictureSelection() {
  for (const [featureId, node] of pictureNodes) {
    node.classList.toggle("selected", featureId === selectedFeatureId);
  }
}

function displayPictureScale(scale, preserveCenter = true) {
  if (!pictureLayout) return;
  const scroller = document.querySelector("#picture-scroll");
  const svg = document.querySelector("#picture-svg");
  const oldWidth = pictureLayout.width * pictureScale;
  const centerRatio = oldWidth
    ? (scroller.scrollLeft + scroller.clientWidth / 2) / oldWidth
    : 0.5;
  pictureScale = scale;
  svg.style.width = `${pictureLayout.width * pictureScale}px`;
  svg.style.height = `${pictureLayout.height * pictureScale}px`;
  requestAnimationFrame(() => {
    scroller.scrollLeft = preserveCenter
      ? centerRatio * pictureLayout.width * pictureScale - scroller.clientWidth / 2
      : 0;
  });
}

function refreshPictureScale(preserveCenter = true) {
  if (!pictureLayout) return;
  const scroller = document.querySelector("#picture-scroll");
  const oldFit = pictureFitScale;
  pictureFitScale = Math.min(1, scroller.clientWidth / pictureLayout.width);
  const scale = pictureScale <= oldFit + 0.001
    ? pictureFitScale
    : Math.max(pictureFitScale, Math.min(2, pictureScale));
  displayPictureScale(scale, preserveCenter);
}

function setPictureScale(scale) {
  displayPictureScale(Math.max(pictureFitScale, Math.min(2, scale)));
}

function changeView(view, filterDecisions = false) {
  if (view === "picture" && currentView === "text") showDecisions = false;
  const enteringPicture = view === "picture" && currentView !== "picture";
  if (view === "text" && filterDecisions) showDecisions = true;
  currentView = view;
  document.querySelector("#picture-view").hidden = view !== "picture";
  document.querySelector("#text-view").hidden = view !== "text";
  document.querySelector("#picture-tools").hidden = view !== "picture";
  document.querySelector("#text-tools").hidden = view !== "text";
  document.querySelector("#view-picture").setAttribute("aria-pressed", String(view === "picture"));
  document.querySelector("#view-text").setAttribute("aria-pressed", String(view === "text"));
  if (enteringPicture) refreshPictureScale();
  applyVisibility();
}

function openRelatedFeature(featureId) {
  if (currentView === "picture") {
    openDetails(featureId);
    const node = pictureNodes.get(featureId);
    if (node) node.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  } else {
    revealFeature(featureId);
    openDetails(featureId);
  }
}
