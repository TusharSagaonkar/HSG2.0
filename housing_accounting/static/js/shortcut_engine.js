/**
 * Keyboard Shortcut Engine for Housing Accounting System
 * 
 * Features:
 * - Loads shortcuts from /administration/api/shortcuts/
 * - Normalizes key combinations for consistent matching
 * - Global key listener (ignores input fields)
 * - Executes actions: URL redirect, HTMX modal, custom JS
 * - Page-specific shortcut support
 */

// Global shortcuts storage
let SHORTCUTS = {};

// Page identifier (set via data-page attribute on body)
let CURRENT_PAGE = null;

/**
 * Normalize key combination from keyboard event
 * @param {KeyboardEvent} e - Keyboard event
 * @returns {string} Normalized key combination (e.g., "CTRL+ALT+R")
 */
function normalizeKeyCombo(e) {
    const keys = [];

    // Modifier keys
    if (e.ctrlKey) keys.push('CTRL');
    if (e.altKey) keys.push('ALT');
    if (e.shiftKey) keys.push('SHIFT');
    if (e.metaKey) keys.push('META');

    // Main key (ignore modifier keys when pressed alone)
    const key = e.key.toUpperCase();
    const modifierKeys = ['CONTROL', 'SHIFT', 'ALT', 'META', 'OS', 'CONTEXTMENU'];
    
    if (!modifierKeys.includes(key) && key !== '') {
        // Map some special keys
        const keyMap = {
            'ESCAPE': 'ESC',
            ' ': 'SPACE',
            'ARROWUP': 'UP',
            'ARROWDOWN': 'DOWN',
            'ARROWLEFT': 'LEFT',
            'ARROWRIGHT': 'RIGHT',
            '/': '?',           // Map slash to question mark (Shift+/ produces ?)
            '?': '?',           // Direct question mark support
        };
        
        keys.push(keyMap[key] || key);
    }

    return keys.join('+');
}

/**
 * Load shortcuts from API
 * @param {string} page - Optional page identifier
 * @returns {Promise<void>}
 */
async function loadShortcuts(page = null) {
    try {
        const params = new URLSearchParams();
        if (page) {
            params.append('page', page);
        }
        
        const response = await fetch(`/administration/api/shortcuts/?${params}`);
        
        if (!response.ok) {
            console.warn('Failed to load shortcuts:', response.status);
            return;
        }
        
        const shortcuts = await response.json();
        
        // Clear existing shortcuts
        SHORTCUTS = {};
        
        // Index by normalized key
        shortcuts.forEach(shortcut => {
            SHORTCUTS[shortcut.key] = {
                type: shortcut.type,
                value: shortcut.value,
                name: shortcut.name,
                scope: shortcut.scope,
                page: shortcut.page,
            };
        });
        
        console.log(`Loaded ${shortcuts.length} keyboard shortcuts`);
    } catch (error) {
        console.error('Error loading shortcuts:', error);
    }
}

/**
 * Execute a shortcut action
 * @param {Object} shortcut - Shortcut object
 */
function executeShortcut(shortcut) {
    console.log(`Executing shortcut: ${shortcut.name} (${shortcut.type}: ${shortcut.value})`);
    
    switch (shortcut.type) {
        case 'URL':
            // URL redirect
            window.location.href = shortcut.value;
            break;
            
        case 'MODAL':
            // Open modal via HTMX
            if (typeof htmx !== 'undefined') {
                htmx.ajax('GET', shortcut.value, {
                    target: '#modal-container',
                    swap: 'innerHTML',
                });
                
                // Show modal if Bootstrap is available
                if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
                    const modalElement = document.getElementById('modal-container');
                    if (modalElement) {
                        const modal = new bootstrap.Modal(modalElement);
                        modal.show();
                    }
                }
            } else {
                console.warn('HTMX not loaded for modal shortcut');
                window.location.href = shortcut.value;
            }
            break;
            
        case 'JS':
            // Custom JavaScript action
            try {
                // Safer alternative to eval: look for function in global scope
                const funcName = shortcut.value.replace(/\(\)$/, '');
                if (typeof window[funcName] === 'function') {
                    window[funcName]();
                } else {
                    // Fallback to eval (use with caution)
                    // eslint-disable-next-line no-eval
                    eval(shortcut.value);
                }
            } catch (error) {
                console.error('Error executing JS shortcut:', error);
            }
            break;
            
        default:
            console.warn(`Unknown shortcut type: ${shortcut.type}`);
    }
}

/**
 * Check if a key combination is a browser default shortcut
 * @param {string} combo - Normalized key combination
 * @param {KeyboardEvent} e - Original keyboard event
 * @returns {boolean} True if it's a browser default shortcut
 */
function isBrowserShortcut(combo, e) {
    // Critical browser navigation shortcuts
    const browserShortcuts = [
        // Navigation
        'CTRL+N', 'CTRL+T', 'CTRL+W', 'CTRL+SHIFT+T',
        'CTRL+TAB', 'CTRL+SHIFT+TAB', 'CTRL+1', 'CTRL+2', 'CTRL+3',
        'CTRL+4', 'CTRL+5', 'CTRL+6', 'CTRL+7', 'CTRL+8', 'CTRL+9',
        
        // Page control
        'CTRL+R', 'CTRL+SHIFT+R', 'CTRL+S', 'CTRL+P',
        
        // Find/View
        'CTRL+F', 'CTRL+G', 'CTRL+U', 'CTRL+SHIFT+I',
        
        // Bookmarks
        'CTRL+D', 'CTRL+SHIFT+D', 'CTRL+SHIFT+B', 'CTRL+SHIFT+O',
        
        // Address bar
        'CTRL+L', 'CTRL+K', 'CTRL+E', 'F6',
        
        // History/Downloads
        'CTRL+J', 'CTRL+SHIFT+DELETE',
        
        // Function keys (browser defaults)
        'F1', 'F3', 'F5', 'F11', 'F12',
    ];
    
    // Check exact matches
    if (browserShortcuts.includes(combo)) {
        return true;
    }
    
    // Special case: Ctrl+H is used by our app for Home, so don't block it
    if (combo === 'CTRL+H') {
        return false;
    }
    
    // Check for Ctrl+Shift+ combinations (most browser dev tools)
    if (combo.startsWith('CTRL+SHIFT+') && combo.length > 11) {
        const key = combo.substring(11);
        // Allow some Ctrl+Shift+ combinations that we use
        const allowedCtrlShift = ['H', 'A', 'R', 'B', 'P', 'M', 'F', 'E', 'N', 'S', '?'];
        if (!allowedCtrlShift.includes(key)) {
            return true;
        }
    }
    
    // Check for Alt-based shortcuts (browser menu navigation)
    if (e.altKey && !e.ctrlKey && !e.shiftKey) {
        // Alt alone or with function keys often triggers browser menus
        return true;
    }
    
    return false;
}

/**
 * Global keydown event handler
 * @param {KeyboardEvent} e - Keyboard event
 */
function handleKeyDown(e) {
    // Ignore if user is typing in input, textarea, or contenteditable
    const activeElement = document.activeElement;
    const tagName = activeElement.tagName;
    const isContentEditable = activeElement.isContentEditable;
    const isInput = ['INPUT', 'TEXTAREA', 'SELECT'].includes(tagName);
    
    if (isInput || isContentEditable) {
        return;
    }
    
    // Normalize key combination
    const combo = normalizeKeyCombo(e);
    
    // Check if combo exists in shortcuts
    if (SHORTCUTS[combo]) {
        e.preventDefault();
        e.stopPropagation();
        executeShortcut(SHORTCUTS[combo]);
        return;
    }
    
    // Prevent browser default shortcuts
    if (isBrowserShortcut(combo, e)) {
        e.preventDefault();
        e.stopPropagation();
        console.log(`Browser shortcut blocked: ${combo}`);
    }
}

/**
 * Initialize the shortcut engine
 * @param {Object} options - Configuration options
 * @param {string} options.page - Page identifier
 * @param {boolean} options.autoLoad - Auto-load shortcuts on init
 */
function initShortcutEngine(options = {}) {
    const { page = null, autoLoad = true } = options;
    
    // Set current page
    CURRENT_PAGE = page || document.body.dataset.page || null;
    
    // Load shortcuts if autoLoad is true
    if (autoLoad) {
        loadShortcuts(CURRENT_PAGE).then(() => {
            console.log('Shortcut engine initialized');
            // Add help shortcut after loading
            addHelpShortcut();
        });
    } else {
        // Still add help shortcut
        addHelpShortcut();
    }
    
    // Add global event listener
    document.addEventListener('keydown', handleKeyDown);
    
    // Return API for manual control
    return {
        reload: () => loadShortcuts(CURRENT_PAGE),
        getShortcuts: () => SHORTCUTS,
        execute: (keyCombo) => {
            if (SHORTCUTS[keyCombo]) {
                executeShortcut(SHORTCUTS[keyCombo]);
                return true;
            }
            return false;
        },
        showHelp: () => showShortcutHelp(),
    };
}

/**
 * Add help shortcut (Ctrl+Q) to show available shortcuts
 */
function addHelpShortcut() {
    // Add to SHORTCUTS manually since it's a built-in help feature
    SHORTCUTS['CTRL+Q'] = {
        key: 'CTRL+Q',
        name: 'Show Keyboard Shortcuts Help',
        type: 'JS',
        value: 'showShortcutHelp',
        scope: 'GLOBAL',
        page: null,
    };
}

/**
 * Show modal with available shortcuts for current page
 */
function showShortcutHelp() {
    // Create or get modal container
    let modal = document.getElementById('shortcut-help-modal');
    
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'shortcut-help-modal';
        modal.className = 'modal fade';
        modal.tabIndex = -1;
        modal.setAttribute('aria-hidden', 'true');
        modal.innerHTML = `
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">Available Keyboard Shortcuts</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        <div class="mb-3">
                            <span class="badge bg-primary">Current Page:</span>
                            <code>${CURRENT_PAGE || 'Global'}</code>
                        </div>
                        <div id="shortcut-help-content">
                            <p>Loading shortcuts...</p>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }
    
    // Populate with shortcuts
    const content = document.getElementById('shortcut-help-content');
    if (content) {
        const shortcuts = Object.values(SHORTCUTS);
        
        if (shortcuts.length === 0) {
            content.innerHTML = '<p class="text-muted">No shortcuts available for your role on this page.</p>';
        } else {
            // Group by scope
            const globalShortcuts = shortcuts.filter(s => s.scope === 'GLOBAL');
            const pageShortcuts = shortcuts.filter(s => s.scope === 'PAGE' && s.page === CURRENT_PAGE);
            
            let html = '';
            
            if (globalShortcuts.length > 0) {
                html += '<h6>Global Shortcuts</h6>';
                html += '<ul class="list-group mb-3">';
                globalShortcuts.forEach(shortcut => {
                    html += `
                        <li class="list-group-item d-flex justify-content-between align-items-center">
                            <div>
                                <span class="badge bg-info me-2">${shortcut.key}</span>
                                <strong>${shortcut.name}</strong>
                                <small class="text-muted d-block">${shortcut.type}: ${shortcut.value}</small>
                            </div>
                        </li>
                    `;
                });
                html += '</ul>';
            }
            
            if (pageShortcuts.length > 0) {
                html += '<h6>Page-Specific Shortcuts</h6>';
                html += '<ul class="list-group mb-3">';
                pageShortcuts.forEach(shortcut => {
                    html += `
                        <li class="list-group-item d-flex justify-content-between align-items-center">
                            <div>
                                <span class="badge bg-success me-2">${shortcut.key}</span>
                                <strong>${shortcut.name}</strong>
                                <small class="text-muted d-block">${shortcut.type}: ${shortcut.value}</small>
                            </div>
                        </li>
                    `;
                });
                html += '</ul>';
            }
            
            content.innerHTML = html;
        }
    }
    
    // Show modal using Bootstrap
    const modalInstance = new bootstrap.Modal(modal);
    modalInstance.show();
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.ShortcutEngine = initShortcutEngine();
    });
} else {
    window.ShortcutEngine = initShortcutEngine();
}

// Export for module usage (if using ES6 modules)
if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
        initShortcutEngine,
        normalizeKeyCombo,
        loadShortcuts,
        executeShortcut,
        showShortcutHelp,
    };
}