/**
 * Vehicle Form - Structure, unit autocomplete, and registration input enhancements.
 */

(function() {
  'use strict';

  document.addEventListener('DOMContentLoaded', function() {
    const societySelect = document.getElementById('id_society');
    const structureSelect = document.getElementById('id_structure');
    const unitSelect = document.getElementById('id_unit');
    const vehicleNumberInput = document.getElementById('id_vehicle_number');

    if (vehicleNumberInput) {
      vehicleNumberInput.addEventListener('input', function() {
        this.value = this.value.toUpperCase();
      });
    }

    if (!structureSelect || !unitSelect) return;

    const autocomplete = enhanceUnitSelection(unitSelect);

    structureSelect.addEventListener('change', function() {
      const societyId = societySelect ? societySelect.value || '' : '';
      const structureId = this.value || '';

      if (societyId && structureId) {
        loadUnitsByStructure(societyId, structureId, unitSelect);
      } else {
        resetUnitSelect(unitSelect);
      }
    });

    if (societySelect) {
      societySelect.addEventListener('change', function() {
        resetStructureSelect(structureSelect);
        resetUnitSelect(unitSelect);
      });
    }

    unitSelect.addEventListener('change', function() {
      syncAutocompleteFromSelect(unitSelect, autocomplete.input);
    });
  });

  function loadUnitsByStructure(societyId, structureId, unitSelect) {
    const params = new URLSearchParams({ society: societyId, structure: structureId });
    const url = `/parking/vehicles/units/?${params.toString()}`;

    fetch(url, { headers: { Accept: 'application/json' } })
      .then(response => {
        if (!response.ok) throw new Error(`Unable to load flats (${response.status})`);
        return response.json();
      })
      .then(data => {
        populateUnitSelect(data.units || [], unitSelect);
      })
      .catch(error => {
        console.error('Error loading units:', error);
      });
  }

  function populateUnitSelect(units, selectElement) {
    const emptyOption = selectElement.querySelector('option[value=""]');
    selectElement.innerHTML = '';
    if (emptyOption) {
      selectElement.appendChild(emptyOption);
    }

    units.forEach(unit => {
      const option = document.createElement('option');
      option.value = unit.id;
      option.textContent = `${unit.identifier} (${unit.unit_type})`;
      option.dataset.structureName = unit.structure__name || '';
      selectElement.appendChild(option);
    });

    resetAutocomplete(selectElement);
  }

  function resetStructureSelect(selectElement) {
    selectElement.value = '';
  }

  function resetUnitSelect(selectElement) {
    const options = selectElement.querySelectorAll('option:not(:first-child)');
    options.forEach(option => option.remove());
    selectElement.value = '';
    resetAutocomplete(selectElement);
  }

  function enhanceUnitSelection(selectElement) {
    const container = selectElement.parentElement;
    const existingInput = container.querySelector('[data-unit-search]');
    if (existingInput) {
      return {
        input: existingInput,
        list: container.querySelector('[data-unit-results]'),
      };
    }

    const wrapper = document.createElement('div');
    wrapper.className = 'vehicle-unit-autocomplete';

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-control form-control-sm';
    input.placeholder = 'Start typing flat number or building';
    input.setAttribute('data-unit-search', '');
    input.setAttribute('aria-label', 'Search and select flat');
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-expanded', 'false');
    input.id = 'unit-search-input';

    const results = document.createElement('div');
    results.className = 'vehicle-unit-results d-none';
    results.setAttribute('data-unit-results', '');
    results.setAttribute('role', 'listbox');

    wrapper.appendChild(input);
    wrapper.appendChild(results);
    container.insertBefore(wrapper, selectElement);
    selectElement.classList.add('vehicle-unit-select-fallback');

    input.addEventListener('input', function() {
      selectElement.value = '';
      renderAutocompleteResults(selectElement, input, results, this.value);
    });

    input.addEventListener('focus', function() {
      renderAutocompleteResults(selectElement, input, results, this.value);
    });

    input.addEventListener('keydown', function(event) {
      handleAutocompleteKeys(event, input, results);
    });

    document.addEventListener('click', function(event) {
      if (!wrapper.contains(event.target)) {
        hideResults(input, results);
      }
    });

    syncAutocompleteFromSelect(selectElement, input);
    return { input: input, list: results };
  }

  function getUnitOptions(selectElement) {
    const rows = [];
    const optgroups = selectElement.querySelectorAll('optgroup');

    if (optgroups.length) {
      optgroups.forEach(group => {
        const groupName = group.getAttribute('label') || '';
        group.querySelectorAll('option').forEach(option => {
          if (!option.value) return;
          rows.push({ option: option, structure: groupName, label: option.textContent.trim() });
        });
      });
      return rows;
    }

    selectElement.querySelectorAll('option').forEach(option => {
      if (!option.value) return;
      rows.push({
        option: option,
        structure: option.dataset.structureName || '',
        label: option.textContent.trim(),
      });
    });
    return rows;
  }

  function renderAutocompleteResults(selectElement, input, results, searchTerm) {
    const term = searchTerm.toLowerCase().trim();
    const matches = getUnitOptions(selectElement)
      .filter(row => {
        if (!term) return true;
        return row.label.toLowerCase().includes(term) || row.structure.toLowerCase().includes(term);
      })
      .slice(0, 12);

    results.innerHTML = '';

    if (!matches.length) {
      const empty = document.createElement('div');
      empty.className = 'vehicle-unit-results__empty';
      empty.textContent = term ? 'No matching flats found' : 'Select society and building to load flats';
      results.appendChild(empty);
      showResults(input, results);
      return;
    }

    matches.forEach((row, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'vehicle-unit-result';
      button.setAttribute('role', 'option');
      button.setAttribute('data-unit-result', '');
      button.setAttribute('data-index', String(index));
      button.innerHTML = `
        <span class="vehicle-unit-result__title">${escapeHtml(row.label)}</span>
        ${row.structure ? `<span class="vehicle-unit-result__meta">${escapeHtml(row.structure)}</span>` : ''}
      `;
      button.addEventListener('click', function() {
        selectUnitOption(selectElement, input, results, row.option);
      });
      results.appendChild(button);
    });

    showResults(input, results);
  }

  function handleAutocompleteKeys(event, input, results) {
    const items = Array.from(results.querySelectorAll('[data-unit-result]'));
    if (!items.length || results.classList.contains('d-none')) return;

    const active = results.querySelector('.is-active');
    let index = active ? Number(active.dataset.index) : -1;

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      index = Math.min(index + 1, items.length - 1);
      setActiveResult(items, index);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      index = Math.max(index - 1, 0);
      setActiveResult(items, index);
    } else if (event.key === 'Enter' && active) {
      event.preventDefault();
      active.click();
    } else if (event.key === 'Escape') {
      hideResults(input, results);
    }
  }

  function setActiveResult(items, index) {
    items.forEach(item => item.classList.remove('is-active'));
    if (items[index]) {
      items[index].classList.add('is-active');
      items[index].scrollIntoView({ block: 'nearest' });
    }
  }

  function selectUnitOption(selectElement, input, results, option) {
    selectElement.value = option.value;
    input.value = buildAutocompleteLabel(option);
    hideResults(input, results);
    selectElement.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function syncAutocompleteFromSelect(selectElement, input) {
    const selected = selectElement.options[selectElement.selectedIndex];
    input.value = selected && selected.value ? buildAutocompleteLabel(selected) : '';
  }

  function resetAutocomplete(selectElement) {
    const input = selectElement.parentElement.querySelector('[data-unit-search]');
    const results = selectElement.parentElement.querySelector('[data-unit-results]');
    if (input) input.value = '';
    if (results) hideResults(input, results);
  }

  function buildAutocompleteLabel(option) {
    const structure = option.closest('optgroup')?.getAttribute('label') || option.dataset.structureName || '';
    const label = option.textContent.trim();
    return structure ? `${label} - ${structure}` : label;
  }

  function showResults(input, results) {
    results.classList.remove('d-none');
    input.setAttribute('aria-expanded', 'true');
  }

  function hideResults(input, results) {
    if (!results) return;
    results.classList.add('d-none');
    if (input) input.setAttribute('aria-expanded', 'false');
  }

  function escapeHtml(value) {
    const element = document.createElement('div');
    element.textContent = value;
    return element.innerHTML;
  }

  window.vehicleFormEnhancements = {
    enhanceUnitSelection: enhanceUnitSelection,
    renderAutocompleteResults: renderAutocompleteResults,
    getUnitOptions: getUnitOptions,
  };
})();
