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

// Sequential shortcut state
const SEQUENCE_TIMEOUT_MS = 1000;
let PENDING_SEQUENCE = null;
let SEQUENCE_TIMER = null;

// Built-in browser-safe commands that are not database-driven yet
const BUILTIN_COMMANDS = [
    { key: 'g h', name: 'Go to Home', type: 'URL', value: '/' },
    { key: 'g d', name: 'Go to Dashboard', type: 'URL', value: '/housing/' },
    { key: 'g a', name: 'Go to Accounting', type: 'URL', value: '/accounting/' },
    { key: 'g r', name: 'Go to Reports', type: 'URL', value: '/reports/' },
    { key: 'g b', name: 'Go to Billing', type: 'URL', value: '/billing/' },
    { key: 'g m', name: 'Go to Members', type: 'URL', value: '/members/' },
    { key: 'g p', name: 'Go to Parking', type: 'URL', value: '/parking/' },
    { key: 'g v', name: 'Open Voucher Entry', type: 'URL', value: '/accounting/vouchers/entry/' },
];

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
                key: shortcut.key,
                type: shortcut.type,
                value: shortcut.value,
                name: shortcut.name,
                scope: shortcut.scope,
                page: shortcut.page,
            };
        });

        addBuiltInShortcuts();
        
        console.log(`Loaded ${shortcuts.length} keyboard shortcuts`);
    } catch (error) {
        console.error('Error loading shortcuts:', error);
        addBuiltInShortcuts();
    }
}

function clearSequenceState() {
    if (SEQUENCE_TIMER) {
        clearTimeout(SEQUENCE_TIMER);
        SEQUENCE_TIMER = null;
    }
    PENDING_SEQUENCE = null;
}

function startSequence(prefix) {
    clearSequenceState();
    PENDING_SEQUENCE = prefix;
    SEQUENCE_TIMER = window.setTimeout(() => {
        clearSequenceState();
    }, SEQUENCE_TIMEOUT_MS);
}

function getMergedCommands() {
    const seen = new Set();
    const commands = [];

    const pushCommand = (command) => {
        const uniqueKey = `${command.key || ''}|${command.name || ''}|${command.type || ''}|${command.value || ''}`;
        if (seen.has(uniqueKey)) {
            return;
        }
        seen.add(uniqueKey);
        commands.push(command);
    };

    BUILTIN_COMMANDS.forEach(pushCommand);
    Object.values(SHORTCUTS).forEach(pushCommand);

    return commands;
}

function getSequenceCommands() {
    return BUILTIN_COMMANDS.map(command => ({ ...command }));
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

    // If we are waiting for a sequence, try to resolve it first.
    if (PENDING_SEQUENCE) {
        const prefix = PENDING_SEQUENCE;

        if (combo === 'ESC') {
            e.preventDefault();
            e.stopPropagation();
            clearSequenceState();
            return;
        }

        if (prefix === 'G') {
            const sequenceMap = {
                H: { key: 'g h', name: 'Go to Home', type: 'URL', value: '/' },
                D: { key: 'g d', name: 'Go to Dashboard', type: 'URL', value: '/housing/' },
                A: { key: 'g a', name: 'Go to Accounting', type: 'URL', value: '/accounting/' },
                R: { key: 'g r', name: 'Go to Reports', type: 'URL', value: '/reports/' },
                B: { key: 'g b', name: 'Go to Billing', type: 'URL', value: '/billing/' },
                M: { key: 'g m', name: 'Go to Members', type: 'URL', value: '/members/' },
                P: { key: 'g p', name: 'Go to Parking', type: 'URL', value: '/parking/' },
                V: { key: 'g v', name: 'Open Voucher Entry', type: 'URL', value: '/accounting/vouchers/entry/' },
            };

            if (sequenceMap[combo]) {
                e.preventDefault();
                e.stopPropagation();
                executeShortcut(sequenceMap[combo]);
                clearSequenceState();
                return;
            }
        }

        // Sequence did not resolve. Clear it and allow the current key to be
        // processed normally below.
        clearSequenceState();
    }

    // Start sequence mode when the prefix key is pressed.
    if (combo === 'G' && !e.ctrlKey && !e.altKey && !e.metaKey) {
        e.preventDefault();
        e.stopPropagation();
        startSequence('G');
        return;
    }
    
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
            // Add built-in shortcuts after loading
            addBuiltInShortcuts();
        });
    } else {
        // Still add built-in shortcuts
        addBuiltInShortcuts();
    }
    
    // Add global event listener
    document.addEventListener('keydown', handleKeyDown);
    
    // Return API for manual control
    return {
        reload: () => loadShortcuts(CURRENT_PAGE),
        getShortcuts: () => SHORTCUTS,
        execute: (keyCombo) => {
            const normalizedKey = String(keyCombo || '').trim().toUpperCase().replace(/\s+/g, ' ');

            if (SHORTCUTS[keyCombo] || SHORTCUTS[normalizedKey]) {
                executeShortcut(SHORTCUTS[keyCombo] || SHORTCUTS[normalizedKey]);
                return true;
            }

            const builtInCommand = BUILTIN_COMMANDS.find(command => command.key.toUpperCase() === normalizedKey);
            if (builtInCommand) {
                executeShortcut(builtInCommand);
                return true;
            }

            return false;
        },
        showHelp: () => showShortcutHelp(),
        showCommandPalette: () => openCommandPalette(),
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
 * Add built-in shortcuts that are not currently stored in the database.
 */
function addBuiltInShortcuts() {
    addHelpShortcut();

    SHORTCUTS['CTRL+K'] = {
        key: 'CTRL+K',
        name: 'Open Command Palette',
        type: 'JS',
        value: 'openCommandPalette()',
        scope: 'GLOBAL',
        page: null,
    };
}

/**
 * Show modal with available shortcuts for current page
 */
function showShortcutHelp() {
    clearSequenceState();

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
                        <div class="alert alert-info py-2 small mb-3">
                            Use the navbar <strong>Shortcuts</strong> button or press <kbd>Ctrl</kbd>+<kbd>Q</kbd> to open this dialog.
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
        const sequenceCommands = getSequenceCommands();
        
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
                        <li class="list-group-item p-0 border-0 mb-2">
                            <button
                                type="button"
                                class="btn btn-outline-info w-100 text-start shortcut-launcher"
                                data-shortcut-key="${shortcut.key}"
                            >
                                <div class="d-flex justify-content-between align-items-start gap-3">
                                    <div>
                                        <span class="badge bg-info me-2">${shortcut.key}</span>
                                        <strong>${shortcut.name}</strong>
                                        <small class="text-muted d-block">${shortcut.type}: ${shortcut.value}</small>
                                    </div>
                                    <span class="badge bg-light text-dark">${shortcut.scope}</span>
                                </div>
                            </button>
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
                        <li class="list-group-item p-0 border-0 mb-2">
                            <button
                                type="button"
                                class="btn btn-outline-success w-100 text-start shortcut-launcher"
                                data-shortcut-key="${shortcut.key}"
                            >
                                <div class="d-flex justify-content-between align-items-start gap-3">
                                    <div>
                                        <span class="badge bg-success me-2">${shortcut.key}</span>
                                        <strong>${shortcut.name}</strong>
                                        <small class="text-muted d-block">${shortcut.type}: ${shortcut.value}</small>
                                    </div>
                                    <span class="badge bg-light text-dark">${shortcut.page || ''}</span>
                                </div>
                            </button>
                        </li>
                    `;
                });
                html += '</ul>';
            }

            if (sequenceCommands.length > 0) {
                html += '<h6>Sequential Navigation</h6>';
                html += '<ul class="list-group mb-3">';
                sequenceCommands.forEach(command => {
                    html += `
                        <li class="list-group-item p-0 border-0 mb-2">
                            <button
                                type="button"
                                class="btn btn-outline-warning w-100 text-start shortcut-launcher"
                                data-shortcut-key="${command.key}"
                            >
                                <div class="d-flex justify-content-between align-items-start gap-3">
                                    <div>
                                        <span class="badge bg-warning text-dark me-2">${command.key}</span>
                                        <strong>${command.name}</strong>
                                        <small class="text-muted d-block">${command.type}: ${command.value}</small>
                                    </div>
                                    <span class="badge bg-light text-dark">Sequence</span>
                                </div>
                            </button>
                        </li>
                    `;
                });
                html += '</ul>';
            }
            
            content.innerHTML = html;
            content.querySelectorAll("[data-shortcut-key]").forEach((button) => {
                button.addEventListener("click", () => {
                    const shortcut = getMergedCommands().find((item) => item.key === button.dataset.shortcutKey);
                    if (!shortcut) {
                        return;
                    }
                    executeShortcut(shortcut);
                    const modalElement = document.getElementById('shortcut-help-modal');
                    if (modalElement && typeof bootstrap !== 'undefined' && bootstrap.Modal) {
                        const instance = bootstrap.Modal.getInstance(modalElement);
                        if (instance) {
                            instance.hide();
                        }
                    }
                });
            });
        }
    }
    
    // Show modal using Bootstrap
    const modalInstance = new bootstrap.Modal(modal);
    modalInstance.show();
}

/**
 * Open the command palette modal.
 */
function openCommandPalette() {
    clearSequenceState();

    let modal = document.getElementById('shortcut-command-palette-modal');

    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'shortcut-command-palette-modal';
        modal.className = 'modal fade';
        modal.tabIndex = -1;
        modal.setAttribute('aria-hidden', 'true');
        modal.innerHTML = `
            <div class="modal-dialog modal-dialog-centered modal-lg">
                <div class="modal-content">
                    <div class="modal-header">
                        <div>
                            <h5 class="modal-title mb-0">Command Palette</h5>
                            <small class="text-muted">Search shortcuts, navigation, and actions</small>
                        </div>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body">
                        <input
                            id="shortcut-command-palette-input"
                            type="search"
                            class="form-control form-control-lg mb-3"
                            placeholder="Type a command or shortcut..."
                            autocomplete="off"
                            spellcheck="false"
                        />
                        <div id="shortcut-command-palette-results" class="list-group"></div>
                    </div>
                    <div class="modal-footer justify-content-between">
                        <small class="text-muted">Enter to run, Esc to close, arrows to move</small>
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }

    const input = modal.querySelector('#shortcut-command-palette-input');
    const results = modal.querySelector('#shortcut-command-palette-results');
    const commands = getMergedCommands().filter(command => command.key !== 'CTRL+K');
    let selectedIndex = 0;

    const render = (query = '') => {
        const normalizedQuery = query.trim().toLowerCase();
        const filtered = commands.filter((command) => {
            if (!normalizedQuery) {
                return true;
            }

            return [
                command.key,
                command.name,
                command.type,
                command.value,
                command.scope,
                command.page,
            ].some((value) => String(value || '').toLowerCase().includes(normalizedQuery));
        });

        if (!filtered.length) {
            results.innerHTML = '<div class="list-group-item text-muted">No matching commands.</div>';
            selectedIndex = 0;
            return;
        }

        if (selectedIndex >= filtered.length) {
            selectedIndex = 0;
        }

        results.innerHTML = filtered.map((command, index) => `
            <button
                type="button"
                class="list-group-item list-group-item-action d-flex justify-content-between align-items-center ${index === selectedIndex ? 'active' : ''}"
                data-command-index="${index}"
            >
                <span>
                    <strong>${command.name}</strong>
                    <small class="d-block ${index === selectedIndex ? 'text-white-50' : 'text-muted'}">${command.type}: ${command.value}</small>
                </span>
                <span class="badge ${index === selectedIndex ? 'bg-light text-dark' : 'bg-secondary'} ms-3">${command.key || ''}</span>
            </button>
        `).join('');

        results.querySelectorAll('[data-command-index]').forEach((button) => {
            button.addEventListener('click', () => {
                const command = filtered[Number.parseInt(button.dataset.commandIndex || '0', 10)];
                if (!command) {
                    return;
                }
                clearSequenceState();
                executeShortcut(command);
                modalInstance.hide();
            });
        });
    };

    const moveSelection = (delta) => {
        const buttons = results.querySelectorAll('[data-command-index]');
        if (!buttons.length) {
            return;
        }

        selectedIndex = (selectedIndex + delta + buttons.length) % buttons.length;
        render(input ? input.value : '');
        const selectedButton = results.querySelector(`[data-command-index="${selectedIndex}"]`);
        if (selectedButton) {
            selectedButton.focus();
        }
    };

    if (input) {
        input.oninput = () => {
            selectedIndex = 0;
            render(input.value);
        };
        input.onkeydown = (event) => {
            if (event.key === 'ArrowDown') {
                event.preventDefault();
                moveSelection(1);
                return;
            }
            if (event.key === 'ArrowUp') {
                event.preventDefault();
                moveSelection(-1);
                return;
            }
            if (event.key === 'Enter') {
                event.preventDefault();
                const currentButtons = results.querySelectorAll('[data-command-index]');
                const selectedButton = currentButtons[selectedIndex];
                if (selectedButton) {
                    selectedButton.click();
                }
                return;
            }
            if (event.key === 'Escape') {
                event.preventDefault();
                modalInstance.hide();
            }
        };
    }

    const modalInstance = new bootstrap.Modal(modal);
    modal.addEventListener('shown.bs.modal', () => {
        if (input) {
            input.value = '';
            selectedIndex = 0;
            render('');
            input.focus();
        }
    }, { once: true });
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
        openCommandPalette,
    };
}
