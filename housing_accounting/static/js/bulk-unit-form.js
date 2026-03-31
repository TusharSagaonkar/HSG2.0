const initBulkUnitForm = () => {
  const form = document.querySelector("[data-bulk-unit-form]");
  if (!form) {
    return;
  }

  const structureField = form.querySelector("#id_structure");
  const floorsField = form.querySelector("#id_floors");
  const unitsPerFloorField = form.querySelector("#id_units_per_floor");
  const startingFloorField = form.querySelector("#id_starting_floor");
  const startingNumberField = form.querySelector("#id_starting_number");
  const numberingStyleField = form.querySelector("#id_numbering_style");
  const defaultUnitTypeField = form.querySelector("#id_default_unit_type");
  const defaultAreaField = form.querySelector("#id_default_area_sqft");
  const defaultChargeableAreaField = form.querySelector(
    "#id_default_chargeable_area_sqft",
  );
  const unitsJsonField = form.querySelector("#id_units_json");
  const gridContainer = form.querySelector("[data-grid-container]");
  const gridSummary = form.querySelector("[data-grid-summary]");
  const selectionSummary = form.querySelector("[data-selection-summary]");
  const generateButton = form.querySelector("[data-generate-grid]");
  const applySelectedButton = form.querySelector("[data-apply-selected]");
  const applyAllActiveButton = form.querySelector("[data-apply-all-active]");
  const toggleSelectedButton = form.querySelector("[data-toggle-selected]");
  const selectSameAreaButton = form.querySelector("[data-select-same-area]");
  const clearSelectionButton = form.querySelector("[data-clear-selection]");
  const bulkTypeField = form.querySelector("[data-bulk-type]");
  const bulkAreaField = form.querySelector("[data-bulk-area]");
  const bulkChargeableAreaField = form.querySelector("[data-bulk-chargeable-area]");

  let gridState = [];
  let selectedKeys = new Set();

  const getCellKey = (cell) => `${cell.floor}:${cell.column}`;

  const formatNumberInput = (value) => {
    if (value === null || value === undefined || value === "") {
      return "";
    }
    return String(value);
  };

  const createGrid = () => {
    const floors = Number.parseInt(floorsField.value || "0", 10);
    const unitsPerFloor = Number.parseInt(unitsPerFloorField.value || "0", 10);
    const startingFloor = Number.parseInt(startingFloorField.value || "1", 10);
    const defaultUnitType = defaultUnitTypeField.value;
    const defaultArea = defaultAreaField.value.trim();
    const defaultChargeableArea = defaultChargeableAreaField.value.trim();

    if (!structureField.value || floors < 1 || unitsPerFloor < 1) {
      return;
    }

    const nextGrid = [];
    let counter = Number.parseInt(startingNumberField.value || "1", 10);

    for (let floorOffset = floors - 1; floorOffset >= 0; floorOffset -= 1) {
      const floorNumber = startingFloor + floorOffset;
      const row = [];

      for (let columnIndex = 0; columnIndex < unitsPerFloor; columnIndex += 1) {
        let identifier = "";
        if (numberingStyleField.value === "floor_based") {
          const sequence = String(
            Number.parseInt(startingNumberField.value || "1", 10) + columnIndex,
          ).padStart(2, "0");
          identifier = `${floorNumber}${sequence}`;
        } else {
          identifier = String(counter);
          counter += 1;
        }

        row.push({
          floor: floorNumber,
          column: columnIndex + 1,
          identifier,
          unit_type: defaultUnitType,
          area_sqft: defaultArea,
          chargeable_area_sqft: defaultChargeableArea,
          is_active: true,
        });
      }

      nextGrid.push(row);
    }

    gridState = nextGrid;
    selectedKeys = new Set();
    syncPayload();
    renderGrid();
  };

  const flattenGrid = () => gridState.flat();

  const updateSelectionSummary = () => {
    if (!selectionSummary) {
      return;
    }

    const cells = flattenGrid();
    const selectedCells = cells.filter((cell) => selectedKeys.has(getCellKey(cell)));
    const activeCount = cells.filter((cell) => cell.is_active).length;
    const inactiveCount = cells.length - activeCount;

    selectionSummary.querySelector("[data-selected-count]").textContent = String(
      selectedCells.length,
    );
    selectionSummary.querySelector("[data-active-count]").textContent = String(activeCount);
    selectionSummary.querySelector("[data-inactive-count]").textContent = String(
      inactiveCount,
    );
  };

  const updateGridSummary = () => {
    if (!gridSummary) {
      return;
    }

    const cells = flattenGrid();
    if (!cells.length) {
      gridSummary.textContent = "No grid generated yet.";
      return;
    }

    const floorCount = gridState.length;
    const unitCount = cells.length;
    gridSummary.textContent = `${floorCount} floors, ${unitCount} units designed`;
  };

  const syncPayload = () => {
    unitsJsonField.value = JSON.stringify(flattenGrid());
    updateSelectionSummary();
    updateGridSummary();
  };

  const getReferenceArea = () => {
    const firstSelected = flattenGrid().find((cell) => selectedKeys.has(getCellKey(cell)));
    if (!firstSelected) {
      return null;
    }
    return firstSelected.area_sqft === "" ? null : String(firstSelected.area_sqft);
  };

  const renderGrid = () => {
    const cells = flattenGrid();
    gridContainer.innerHTML = "";

    if (!cells.length) {
      const emptyState = document.createElement("div");
      emptyState.className = "bulk-empty-state";
      emptyState.textContent = "Choose a structure, set the dimensions, and generate your grid.";
      gridContainer.appendChild(emptyState);
      syncPayload();
      return;
    }

    const tree = document.createElement("ul");
    tree.className = "structure-tree bulk-tree-root";

    const structureNode = document.createElement("li");
    structureNode.className = "structure-node";

    const structureHeader = document.createElement("div");
    structureHeader.className =
      "bulk-structure-header d-flex flex-wrap justify-content-between gap-2 align-items-start";

    const structureMeta = document.createElement("div");
    const structureName = document.createElement("strong");
    const selectedOption = structureField.options[structureField.selectedIndex];
    structureName.textContent = selectedOption ? selectedOption.textContent : "Structure";
    structureMeta.appendChild(structureName);

    const structureType = document.createElement("span");
    structureType.className = "text-muted ms-1";
    structureType.textContent = "(Structure)";
    structureMeta.appendChild(structureType);

    const structureDetails = document.createElement("div");
    structureDetails.className = "small text-muted";
    structureDetails.textContent = `Floors: ${gridState.length} • Units: ${cells.length}`;
    structureMeta.appendChild(structureDetails);
    structureHeader.appendChild(structureMeta);

    const structureHint = document.createElement("div");
    structureHint.className = "small text-muted";
    structureHint.textContent = "Edit units inside each floor section before saving.";
    structureHeader.appendChild(structureHint);
    structureNode.appendChild(structureHeader);

    const floorList = document.createElement("ul");
    floorList.className = "structure-children";
    const referenceArea = getReferenceArea();

    gridState.forEach((row) => {
      const floorNode = document.createElement("li");
      floorNode.className = "structure-node";

      const floorHeader = document.createElement("div");
      floorHeader.className = "bulk-floor-header";

      const floorMeta = document.createElement("div");
      const floorName = document.createElement("strong");
      floorName.textContent = `Floor ${row[0]?.floor || ""}`;
      floorMeta.appendChild(floorName);

      const floorType = document.createElement("span");
      floorType.className = "text-muted ms-1";
      floorType.textContent = "(Floor)";
      floorMeta.appendChild(floorType);

      const floorUnitsCount = document.createElement("div");
      floorUnitsCount.className = "small text-muted";
      floorUnitsCount.textContent = `Units: ${row.length}`;
      floorMeta.appendChild(floorUnitsCount);
      floorHeader.appendChild(floorMeta);

      const floorHint = document.createElement("span");
      floorHint.className = "badge text-bg-light";
      floorHint.textContent = "Selectable unit grid";
      floorHeader.appendChild(floorHint);
      floorNode.appendChild(floorHeader);

      const unitsList = document.createElement("ul");
      unitsList.className = "bulk-floor-units";
      unitsList.style.gridTemplateColumns = `repeat(${row.length}, minmax(210px, 1fr))`;

      row.forEach((cell) => {
        const item = document.createElement("li");
        item.className = "bulk-floor-unit-slot";
        const card = document.createElement("div");
        const cellKey = getCellKey(cell);

        card.className = "bulk-cell";
        if (selectedKeys.has(cellKey)) {
          card.classList.add("is-selected");
        }
        if (!cell.is_active) {
          card.classList.add("is-inactive");
        }
        if (referenceArea && String(cell.area_sqft) === referenceArea) {
          card.classList.add("is-area-match");
        }
        card.dataset.cellKey = cellKey;

        const head = document.createElement("div");
        head.className = "bulk-cell-head";

        const title = document.createElement("div");
        title.className = "bulk-cell-title";
        title.textContent = `F${cell.floor} • #${cell.column}`;
        head.appendChild(title);

        const badge = document.createElement("span");
        badge.className = `badge ${cell.is_active ? "bg-success-subtle text-success" : "bg-secondary-subtle text-secondary"}`;
        badge.textContent = cell.is_active ? "Active" : "Inactive";
        head.appendChild(badge);
        card.appendChild(head);

        const fields = document.createElement("div");
        fields.className = "bulk-cell-fields";

        const identifierInput = document.createElement("input");
        identifierInput.type = "text";
        identifierInput.className = "form-control";
        identifierInput.value = cell.identifier;
        identifierInput.placeholder = "Unit number";
        identifierInput.addEventListener("input", () => {
          cell.identifier = identifierInput.value;
          syncPayload();
        });
        fields.appendChild(identifierInput);

        const typeSelect = document.createElement("select");
        typeSelect.className = "form-select";
        Array.from(defaultUnitTypeField.options).forEach((option) => {
          const selectOption = document.createElement("option");
          selectOption.value = option.value;
          selectOption.textContent = option.textContent;
          selectOption.selected = option.value === cell.unit_type;
          typeSelect.appendChild(selectOption);
        });
        typeSelect.addEventListener("change", () => {
          cell.unit_type = typeSelect.value;
          syncPayload();
        });
        fields.appendChild(typeSelect);

        const areaInput = document.createElement("input");
        areaInput.type = "number";
        areaInput.min = "0";
        areaInput.step = "0.01";
        areaInput.className = "form-control";
        areaInput.value = formatNumberInput(cell.area_sqft);
        areaInput.placeholder = "Area";
        areaInput.addEventListener("input", () => {
          cell.area_sqft = areaInput.value.trim();
          syncPayload();
        });
        areaInput.addEventListener("blur", () => {
          renderGrid();
        });
        fields.appendChild(areaInput);

        const chargeableInput = document.createElement("input");
        chargeableInput.type = "number";
        chargeableInput.min = "0";
        chargeableInput.step = "0.01";
        chargeableInput.className = "form-control";
        chargeableInput.value = formatNumberInput(cell.chargeable_area_sqft);
        chargeableInput.placeholder = "Chargeable area";
        chargeableInput.addEventListener("input", () => {
          cell.chargeable_area_sqft = chargeableInput.value.trim();
          syncPayload();
        });
        fields.appendChild(chargeableInput);

        const activeWrap = document.createElement("label");
        activeWrap.className = "form-check d-flex align-items-center gap-2 mb-0";
        const activeInput = document.createElement("input");
        activeInput.type = "checkbox";
        activeInput.className = "form-check-input mt-0";
        activeInput.checked = cell.is_active;
        activeInput.addEventListener("change", () => {
          cell.is_active = activeInput.checked;
          syncPayload();
          renderGrid();
        });
        const activeLabel = document.createElement("span");
        activeLabel.className = "small";
        activeLabel.textContent = "Save as active";
        activeWrap.appendChild(activeInput);
        activeWrap.appendChild(activeLabel);
        fields.appendChild(activeWrap);

        card.appendChild(fields);
        card.addEventListener("click", (event) => {
          if (event.target.closest("input, select, label")) {
            return;
          }

          if (event.metaKey || event.ctrlKey) {
            if (selectedKeys.has(cellKey)) {
              selectedKeys.delete(cellKey);
            } else {
              selectedKeys.add(cellKey);
            }
          } else {
            const isSameSingleSelection =
              selectedKeys.size === 1 && selectedKeys.has(cellKey);
            selectedKeys = isSameSingleSelection ? new Set() : new Set([cellKey]);
          }

          renderGrid();
        });

        item.appendChild(card);
        unitsList.appendChild(item);
      });

      floorNode.appendChild(unitsList);
      floorList.appendChild(floorNode);
    });

    structureNode.appendChild(floorList);
    tree.appendChild(structureNode);
    gridContainer.appendChild(tree);
    syncPayload();
  };

  const getSelectedCells = () =>
    flattenGrid().filter((cell) => selectedKeys.has(getCellKey(cell)));

  const applyBulkValues = (cells) => {
    if (!cells.length) {
      return;
    }

    const nextType = bulkTypeField.value;
    const nextArea = bulkAreaField.value.trim();
    const nextChargeableArea = bulkChargeableAreaField.value.trim();

    cells.forEach((cell) => {
      if (nextType) {
        cell.unit_type = nextType;
      }
      if (nextArea !== "") {
        cell.area_sqft = nextArea;
      }
      if (nextChargeableArea !== "") {
        cell.chargeable_area_sqft = nextChargeableArea;
      }
    });

    renderGrid();
  };

  generateButton.addEventListener("click", () => {
    createGrid();
  });

  applySelectedButton.addEventListener("click", () => {
    applyBulkValues(getSelectedCells());
  });

  applyAllActiveButton.addEventListener("click", () => {
    applyBulkValues(flattenGrid().filter((cell) => cell.is_active));
  });

  toggleSelectedButton.addEventListener("click", () => {
    const selectedCells = getSelectedCells();
    if (!selectedCells.length) {
      return;
    }
    selectedCells.forEach((cell) => {
      cell.is_active = !cell.is_active;
    });
    renderGrid();
  });

  selectSameAreaButton.addEventListener("click", () => {
    const referenceArea = getReferenceArea();
    if (!referenceArea) {
      return;
    }

    selectedKeys = new Set(
      flattenGrid()
        .filter((cell) => String(cell.area_sqft) === referenceArea)
        .map((cell) => getCellKey(cell)),
    );
    renderGrid();
  });

  clearSelectionButton.addEventListener("click", () => {
    selectedKeys = new Set();
    renderGrid();
  });

  form.addEventListener("submit", () => {
    syncPayload();
  });

  if (unitsJsonField.value.trim()) {
    try {
      const cells = JSON.parse(unitsJsonField.value);
      if (Array.isArray(cells) && cells.length) {
        const grouped = new Map();
        cells.forEach((cell) => {
          const floorKey = String(cell.floor);
          if (!grouped.has(floorKey)) {
            grouped.set(floorKey, []);
          }
          grouped.get(floorKey).push({
            floor: Number(cell.floor),
            column: Number(cell.column),
            identifier: cell.identifier || "",
            unit_type: cell.unit_type || defaultUnitTypeField.value,
            area_sqft: cell.area_sqft ?? "",
            chargeable_area_sqft: cell.chargeable_area_sqft ?? "",
            is_active: Boolean(cell.is_active),
          });
        });

        gridState = Array.from(grouped.values())
          .map((row) => row.sort((left, right) => left.column - right.column))
          .sort((left, right) => right[0].floor - left[0].floor);
        renderGrid();
        return;
      }
    } catch (error) {
      unitsJsonField.value = "";
    }
  }

  syncPayload();
};

window.addEventListener("DOMContentLoaded", () => {
  initBulkUnitForm();
});
