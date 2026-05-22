/**
 * Vehicle Form - Structure & Unit Selection Enhancement
 * Provides hierarchical structure selection with dynamic unit loading and search/filter capability
 */

(function() {
  'use strict';

  // Initialize interactions on page load
  document.addEventListener('DOMContentLoaded', function() {
    const societySelect = document.getElementById('id_society');
    const structureSelect = document.getElementById('id_structure');
    const unitSelect = document.getElementById('id_unit');
    
    if (!structureSelect || !unitSelect) return;

    // Setup structure change listener to load units
    if (structureSelect) {
      structureSelect.addEventListener('change', function() {
        const societyId = societySelect.value || '';
        const structureId = this.value || '';
        
        if (societyId && structureId) {
          loadUnitsByStructure(societyId, structureId, unitSelect);
        } else {
          // Reset unit select
          resetUnitSelect(unitSelect);
        }
      });
    }
    
    // Setup society change to load structures
    if (societySelect) {
      societySelect.addEventListener('change', function() {
        resetStructureSelect(structureSelect);
        resetUnitSelect(unitSelect);
      });
    }

    // Add search enhancement to unit select
    enhanceUnitSelection(unitSelect);
  });

  function loadUnitsByStructure(societyId, structureId, unitSelect) {
    /**
     * Load units for selected structure via AJAX
     */
    const url = `/parking/vehicles/units/?society=${societyId}&structure=${structureId}`;
    
    fetch(url)
      .then(response => response.json())
      .then(data => {
        populateUnitSelect(data.units, unitSelect);
      })
      .catch(error => {
        console.error('Error loading units:', error);
      });
  }

  function populateUnitSelect(units, selectElement) {
    /**
     * Populate unit select with options from server
     */
    // Clear existing options except the empty one
    const options = selectElement.querySelectorAll('option');
    options.forEach((option, index) => {
      if (index > 0) option.remove();
    });
    
    // Add new options
    units.forEach(unit => {
      const option = document.createElement('option');
      option.value = unit.id;
      option.textContent = `${unit.identifier} (${unit.unit_type})`;
      selectElement.appendChild(option);
    });
    
    // Trigger enhancement for new options
    const searchInput = selectElement.parentElement.querySelector('[data-unit-search]');
    if (searchInput) {
      searchInput.value = '';
      const optionsData = parseOptionsData(selectElement);
      filterUnits(selectElement, optionsData, '');
    }
  }

  function resetStructureSelect(selectElement) {
    /**
     * Reset structure select to empty state
     */
    selectElement.value = '';
  }

  function resetUnitSelect(selectElement) {
    /**
     * Reset unit select to empty state
     */
    // Keep only the empty option
    const options = selectElement.querySelectorAll('option:not(:first-child)');
    options.forEach(option => option.remove());
    selectElement.value = '';
  }

  function enhanceUnitSelection(selectElement) {
    // Create a search input above the select
    const container = selectElement.parentElement;
    const searchWrapper = document.createElement('div');
    searchWrapper.className = 'mb-2';
    
    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.className = 'form-control form-control-sm';
    searchInput.placeholder = 'Search units (e.g., "101", "Flat")';
    searchInput.setAttribute('data-unit-search', '');
    searchInput.setAttribute('aria-label', 'Search units');
    
    const label = document.createElement('label');
    label.className = 'form-label small text-muted mb-2';
    label.setAttribute('for', 'unit-search-input');
    label.textContent = 'Quick search:';
    
    searchInput.id = 'unit-search-input';
    searchWrapper.appendChild(label);
    searchWrapper.appendChild(searchInput);
    
    // Insert search before the select
    container.insertBefore(searchWrapper, selectElement);
    
    // Store original options structure
    const optionsData = parseOptionsData(selectElement);
    
    // Add event listener for search
    searchInput.addEventListener('input', function() {
      filterUnits(selectElement, optionsData, this.value);
    });
    
    // Highlight matching text
    selectElement.addEventListener('focus', function() {
      searchInput.focus();
      searchInput.select();
    });
  }

  function parseOptionsData(selectElement) {
    /**
     * Parse optgroups and options into a data structure for quick filtering.
     * Structure: [
     *   { group: "Structure Name", options: [{ value, label }, ...] },
     *   ...
     * ]
     */
    const data = [];
    const optgroups = selectElement.querySelectorAll('optgroup');
    
    optgroups.forEach(group => {
      const groupName = group.getAttribute('label');
      const options = [];
      
      group.querySelectorAll('option').forEach(option => {
        options.push({
          value: option.value,
          label: option.textContent,
          element: option,
        });
      });
      
      data.push({
        group: groupName,
        options: options,
      });
    });
    
    return data;
  }

  function filterUnits(selectElement, optionsData, searchTerm) {
    /**
     * Filter optgroups and options based on search term.
     * Hides non-matching groups and options.
     */
    const term = searchTerm.toLowerCase().trim();
    const optgroups = selectElement.querySelectorAll('optgroup');
    
    optgroups.forEach((group, index) => {
      const groupData = optionsData[index];
      const matchingOptions = [];
      
      group.querySelectorAll('option').forEach(option => {
        const matches = 
          groupData.group.toLowerCase().includes(term) ||
          option.textContent.toLowerCase().includes(term);
        
        // For optgroup, we can't directly hide, but we can disable non-matching
        if (!matches && term) {
          option.disabled = true;
          option.style.display = 'none';
        } else {
          option.disabled = false;
          option.style.display = '';
          if (term && matches) {
            matchingOptions.push(option);
          }
        }
      });
      
      // Hide optgroup if no matching options
      if (term && matchingOptions.length === 0) {
        group.style.display = 'none';
      } else {
        group.style.display = '';
      }
    });
  }

  // Export functions for testing
  window.vehicleFormEnhancements = {
    enhanceUnitSelection: enhanceUnitSelection,
    parseOptionsData: parseOptionsData,
    filterUnits: filterUnits,
  };
})();
