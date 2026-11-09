// DoorOpener keypad. The server passes its settings as data- attributes on <body>;
// BASE is the path DoorOpener is served under ('' on its own port). A shortcut
// page (/barrier, /barrier+door) names its doors in data-doors.
const PAGE = document.body.dataset;
const BASE = PAGE.base || '';
const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]').content;
const SHORTCUT_DOORS = PAGE.doors ? PAGE.doors.split('+') : null;
let currentPin = '';
let isSubmitting = false;
let autoSubmitTimer = null;
let blockCountdownTimer = null;
let choiceTimer = null;
let resultTimer = null;
const RESULT_MS = 1200; // how long a result stays in the PIN box

// By the phone's own clock: the keypad never says whose PIN it was.
function greeting() {
    const hour = new Date().getHours();
    if (hour >= 5 && hour < 12) return 'Good morning';
    if (hour >= 12 && hour < 18) return 'Good afternoon';
    return 'Good evening';
}

function postJson(path, body) {
    return fetch(BASE + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF_TOKEN },
        credentials: 'same-origin',
        cache: 'no-store',
        body: JSON.stringify(body),
    }).then(response => response.json().then(data => ({ status: response.status, data })));
}

document.querySelectorAll('.keypad-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const digit = btn.dataset.digit;
      const action = btn.dataset.action;

      if (digit) {
        addDigit(digit);
      } else if (action === 'clear') {
        clearPin();
      }
    });
});

function addDigit(digit) {
    if (isSubmitting || !document.getElementById('choice').hidden) return;
    if (currentPin.length < 8) {
        currentPin += digit;
        updatePinDisplay();

        // Haptic feedback
        if (window.navigator.vibrate) {
            window.navigator.vibrate(30);
        }

        // Debounced auto-submit: wait until user pauses typing and length >= 4
        if (autoSubmitTimer) {
            clearTimeout(autoSubmitTimer);
            autoSubmitTimer = null;
        }
        if (currentPin.length >= 4) {
            autoSubmitTimer = setTimeout(() => {
                if (!isSubmitting && currentPin.length >= 4) {
                    openDoor();
                }
            }, 700);
        }
    }
}

function clearPin() {
    if (currentPin.length > 0) {
        currentPin = currentPin.slice(0, -1);
        updatePinDisplay();
        if (autoSubmitTimer) {
            clearTimeout(autoSubmitTimer);
            autoSubmitTimer = null;
        }

        // Haptic feedback
        if (window.navigator.vibrate) {
            window.navigator.vibrate(30);
        }
    }
}

function updatePinDisplay() {
    const display = document.getElementById('pinDisplay');
    if (currentPin.length === 0) {
        display.innerHTML = '<span class="pin-placeholder">Enter PIN</span>';
    } else {
        let html = '';
        for (let i = 0; i < currentPin.length; i++) {
            html += `<span class="pin-dot" style="animation-delay:${i * 40}ms">●</span>`;
        }
        display.innerHTML = html;
    }
}

function openDoor() {
    const pin = currentPin;

    if (!pin) {
        showStatus('Please enter a PIN', 'error');
        return;
    }

    if (isSubmitting) return; // Prevent concurrent submissions
    isSubmitting = true;
    if (autoSubmitTimer) { clearTimeout(autoSubmitTimer); autoSubmitTimer = null; }

    const body = { pin: pin };
    if (SHORTCUT_DOORS) body.doors = SHORTCUT_DOORS;
    postJson('/open-door', body)
        .then(({ status, data }) => {
            if (status === 200 && data.status === 'success') {
                celebrate(data.message);
            } else if (status === 200 && data.status === 'choose') {
                showChoice(data);
            } else if (status === 403 && data.status === 'refused') {
                showRefused(data.message);
            } else {
                if (data && data.blocked_until) {
                    startBlockCountdown(data.blocked_until);
                }
                deny(data.message);
            }
        })
        .catch(error => {
            showStatus('Error: Could not connect to server', 'error');
        })
        .finally(() => {
            // Clear the PIN once its result has been seen
            setTimeout(() => {
                currentPin = '';
                updatePinDisplay();
                isSubmitting = false;
            }, RESULT_MS);
        });
}

// The result shows in the PIN box for a moment, as on a phone's lock screen:
// green when a door opens, red with a shake for a wrong PIN, amber for a PIN
// that is right but not for now.
function showResult(state) {
    const display = document.getElementById('pinDisplay');
    display.classList.remove('is-granted', 'is-denied', 'is-refused');
    display.classList.add('is-' + state);
    clearTimeout(resultTimer);
    resultTimer = setTimeout(() => display.classList.remove('is-' + state), RESULT_MS);
}

function celebrate(message) {
    showStatus(greeting() + ' · ' + message, 'success');
    showResult('granted');
    playSuccessSound();
    if (window.navigator.vibrate) {
        // Fancy "ta-da" vibration pattern: short-long-short with crescendo
        window.navigator.vibrate([50, 30, 150, 50, 80, 30, 200]);
    }
    showAccessGrantedPopup();
}

function deny(message) {
    showStatus('Error: ' + message, 'error');
    showResult('denied');
    playFailureSound();
    showAccessDeniedPopup();
    if (window.navigator.vibrate) {
        // Enhanced failure vibration pattern: strong buzz with pauses
        window.navigator.vibrate([200, 100, 200, 100, 300]);
    }
}

// The PIN is right but not for now (its owner's phone gets told why): calm, not an alarm.
function showRefused(message) {
    showStatus(message, 'refused', false, 7000);
    showResult('refused');
    if (window.navigator.vibrate) window.navigator.vibrate(120);
}

// --- Several doors: one tile each, for as long as the pass lasts ---
function setKeypadShown(shown) {
    document.getElementById('keypad').style.display = shown ? 'grid' : 'none';
    document.getElementById('pinDisplay').style.display = shown ? 'flex' : 'none';
    document.getElementById('choice').hidden = shown;
}

function showChoice(data) {
    const tiles = document.getElementById('tiles');
    tiles.textContent = '';
    data.doors.forEach(door => {
        const tile = document.createElement('button');
        tile.type = 'button';
        tile.className = 'tile';
        const icon = document.getElementById('icon-' + door.icon) || document.getElementById('icon-door');
        tile.appendChild(icon.content.cloneNode(true));
        const name = document.createElement('span');
        name.className = 'tile-name';
        name.textContent = door.name;
        tile.appendChild(name);
        if (door.battery) {
            const level = document.createElement('span');
            level.className = 'tile-battery';
            tile.appendChild(level);
            fetchBattery(door.id).then(value => { if (value !== null) level.textContent = '🔋 ' + value + '%'; });
        }
        tile.addEventListener('click', () => chooseDoor(data.pass, door.id, tiles));
        tiles.appendChild(tile);
    });
    document.getElementById('choiceTitle').textContent = greeting() + '. Which one?';
    setKeypadShown(false);

    const countdown = document.getElementById('choiceCountdown');
    const ends = Date.now() + data.expires_in * 1000;
    const tick = () => {
        const left = Math.ceil((ends - Date.now()) / 1000);
        if (left <= 0) {
            closeChoice();
            showStatus('Too slow: enter the PIN again', 'error');
            return;
        }
        countdown.textContent = 'closes in ' + left + ' s';
    };
    if (choiceTimer) clearInterval(choiceTimer);
    choiceTimer = setInterval(tick, 1000);
    tick();
}

function closeChoice() {
    if (choiceTimer) { clearInterval(choiceTimer); choiceTimer = null; }
    setKeypadShown(true);
}

function chooseDoor(pass, doorId, tiles) {
    tiles.querySelectorAll('button').forEach(b => { b.disabled = true; });
    postJson('/open-door/choice', { pass: pass, door: doorId })
        .then(({ status, data }) => {
            closeChoice();
            if (status === 200 && data.status === 'success') celebrate(data.message);
            else deny(data.message);
        })
        .catch(() => {
            closeChoice();
            showStatus('Error: Could not connect to server', 'error');
        });
}

function showStatus(message, type, persistent, duration) {
    const toastContainer = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.textContent = message;
    toastContainer.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('show');
    }, 10);
    if (!persistent) {
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 500);
        }, duration || 3500);
    }
}

function startBlockCountdown(blockedUntilEpoch) {
    try {
        const container = document.getElementById('toast-container');
        if (!container || !blockedUntilEpoch) return;
        let el = document.getElementById('block-toast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'block-toast';
            el.className = 'toast error';
            container.appendChild(el);
            // Animate in
            setTimeout(() => el.classList.add('show'), 10);
        }
        const update = () => {
            const now = Date.now() / 1000;
            const remaining = Math.max(0, Math.ceil(blockedUntilEpoch - now));
            const mins = Math.floor(remaining / 60);
            const secs = remaining % 60;
            el.textContent = `Too many attempts. Try again in ${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
            if (remaining <= 0) {
                clearInterval(blockCountdownTimer);
                blockCountdownTimer = null;
                el.classList.remove('show');
                setTimeout(() => el.remove(), 500);
            }
        };
        if (blockCountdownTimer) clearInterval(blockCountdownTimer);
        blockCountdownTimer = setInterval(update, 1000);
        update();
    } catch (e) { }
}

function updateBatteryLevel(level) {
    const batteryIcon = document.getElementById('battery-icon');
    const batteryLevel = document.getElementById('battery-level');
    if (batteryLevel) {
        batteryLevel.textContent = (level !== null && level !== undefined) ? `${level}%` : '--';
    }

    // Update battery fill based on level
    if (level >= 80) {
        batteryIcon.style.background = 'linear-gradient(90deg, #4CAF50 0%, #8BC34A 100%)';
    } else if (level >= 50) {
        batteryIcon.style.background = 'linear-gradient(90deg, #8BC34A 0%, #CDDC39 100%)';
    } else if (level >= 20) {
        batteryIcon.style.background = 'linear-gradient(90deg, #FFC107 0%, #FF9800 100%)';
    } else {
        batteryIcon.style.background = 'linear-gradient(90deg, #F44336 0%, #E91E63 100%)';
    }

    // Animate the fill width based on battery level
    const fillWidth = level ? `${level}%` : '0%';
    batteryIcon.style.backgroundSize = `${fillWidth} 100%`;
    batteryIcon.style.backgroundRepeat = 'no-repeat';
}

// A door's battery level in %, or null.
function fetchBattery(doorId) {
    return fetch(BASE + '/battery?door=' + encodeURIComponent(doorId), { credentials: 'same-origin', cache: 'no-store' })
        .then(response => response.json())
        .then(data => (data.level === null || data.level === undefined) ? null : Number(data.level))
        .catch(() => null);
}

function fetchBatteryLevel() {
    fetchBattery(PAGE.batteryDoor).then(level => {
        const container = document.getElementById("battery-container");
        if (level === null) {
            container.style.display = "none";
        } else {
            container.style.display = "flex";
            updateBatteryLevel(level);
        }
    });
}

function playSuccessSound() {
    try {
        // Create AudioContext for Web Audio API
        const audioContext = new (window.AudioContext || window.webkitAudioContext)();

        // Success sound: ascending chime with harmonics
        const playTone = (frequency, startTime, duration, volume = 0.3) => {
            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();

            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);

            oscillator.frequency.setValueAtTime(frequency, startTime);
            oscillator.type = 'sine';

            // Smooth volume envelope
            gainNode.gain.setValueAtTime(0, startTime);
            gainNode.gain.linearRampToValueAtTime(volume, startTime + 0.01);
            gainNode.gain.exponentialRampToValueAtTime(0.01, startTime + duration);

            oscillator.start(startTime);
            oscillator.stop(startTime + duration);
        };

        const now = audioContext.currentTime;

        // "Ta-da" chord progression: C-E-G-C (262-330-392-523 Hz)
        playTone(262, now, 0.15, 0.2);        // C4 - short
        playTone(330, now + 0.08, 0.25, 0.25); // E4 - medium  
        playTone(392, now + 0.2, 0.3, 0.3);    // G4 - longer
        playTone(523, now + 0.35, 0.4, 0.35);  // C5 - longest, highest

        // Add subtle harmonics for richness
        playTone(523 * 2, now + 0.35, 0.2, 0.1); // Octave harmonic
    } catch (e) {
        // Audio API not supported, fail silently
    }
}

function playFailureSound() {
    try {
        const audioContext = new (window.AudioContext || window.webkitAudioContext)();
        // Futuristic digital fail: rapid frequency modulation, metallic beeps, glitch
        const now = audioContext.currentTime;
        const glitch = (freqs, start, dur, vol) => {
            freqs.forEach((f, i) => {
                const osc = audioContext.createOscillator();
                const gain = audioContext.createGain();
                osc.connect(gain);
                gain.connect(audioContext.destination);
                osc.type = i % 2 === 0 ? 'triangle' : 'square';
                osc.frequency.setValueAtTime(f, start + i * 0.02);
                // Quick vibrato
                if (osc.type === 'triangle') {
                    osc.frequency.linearRampToValueAtTime(f * 1.07, start + i * 0.02 + dur / 3);
                    osc.frequency.linearRampToValueAtTime(f, start + i * 0.02 + dur);
                }
                gain.gain.setValueAtTime(0, start + i * 0.02);
                gain.gain.linearRampToValueAtTime(vol, start + i * 0.02 + 0.01);
                gain.gain.exponentialRampToValueAtTime(0.01, start + i * 0.02 + dur);
                osc.start(start + i * 0.02);
                osc.stop(start + i * 0.02 + dur);
            });
        };
        // Glitchy metallic sequence
        glitch([420, 1800, 666, 1337, 333, 888], now, 0.09, 0.19);
        glitch([210, 900, 333, 777], now + 0.13, 0.13, 0.13);
        glitch([120, 480], now + 0.28, 0.17, 0.09);
        // Short digital zap
        const osc = audioContext.createOscillator();
        const gain = audioContext.createGain();
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(80, now + 0.38);
        gain.gain.setValueAtTime(0.13, now + 0.38);
        osc.connect(gain);
        gain.connect(audioContext.destination);
        osc.frequency.linearRampToValueAtTime(10, now + 0.45);
        gain.gain.linearRampToValueAtTime(0.01, now + 0.45);
        osc.start(now + 0.38);
        osc.stop(now + 0.45);
    } catch (e) { }
}

// Show Matrix-style ACCESS DENIED popup
function showAccessDeniedPopup() {
    const popup = document.getElementById('accessDeniedPopup');
    if (popup) {
        popup.style.display = 'block';
        setTimeout(() => {
            popup.style.opacity = '1';
            popup.style.top = '-10px';
        }, 10);
        setTimeout(() => {
            popup.style.opacity = '0';
            popup.style.top = '-60px';
            setTimeout(() => { popup.style.display = 'none'; }, 400);
        }, 1700);
    }
}

// Show Matrix-style ACCESS GRANTED popup
function showAccessGrantedPopup() {
    const popup = document.getElementById('accessGrantedPopup');
    if (popup) {
        popup.style.display = 'block';
        setTimeout(() => {
            popup.style.opacity = '1';
            popup.style.top = '-10px';
        }, 10);
        setTimeout(() => {
            popup.style.opacity = '0';
            popup.style.top = '-60px';
            setTimeout(() => { popup.style.display = 'none'; }, 400);
        }, 1700);
    }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function () {
    // Register service worker for PWA install/offline support
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register(BASE + '/service-worker.js?v=' + encodeURIComponent(PAGE.version || '')).catch(() => { /* ignore */ });
    }

    // Battery level: fetch now, then every 60s while the tab is visible. A
    // backgrounded tab pauses polling and refetches when it comes back.
    if (PAGE.batteryDoor) {
        let batteryPollTimer = null;
        const startBatteryPolling = () => {
            if (batteryPollTimer === null) {
                fetchBatteryLevel();
                batteryPollTimer = setInterval(fetchBatteryLevel, 60000);
            }
        };
        const stopBatteryPolling = () => {
            if (batteryPollTimer !== null) {
                clearInterval(batteryPollTimer);
                batteryPollTimer = null;
            }
        };
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) stopBatteryPolling(); else startBatteryPolling();
        });
        if (!document.hidden) startBatteryPolling();
    }

    // Safari on iPhone and iPad shows a key as pressed (:active) only when the
    // page listens for touches. Passive, so it never holds up a tap or a scroll.
    document.addEventListener('touchstart', () => {}, { passive: true });

    // Initialize PIN display
    updatePinDisplay();
    document.getElementById('choiceCancel').addEventListener('click', closeChoice);

    // Add keyboard support; ignore auto-repeat when key is held down
    document.addEventListener('keydown', function (event) {
        if (event.repeat) {
            event.preventDefault();
            return;
        }
        if (event.key >= '0' && event.key <= '9') {
            addDigit(event.key);
            event.preventDefault();
        } else if (event.key === 'Backspace') {
            clearPin();
            event.preventDefault();
        } else if (event.key === 'Enter') {
            if (!isSubmitting && document.getElementById('choice').hidden) openDoor();
            event.preventDefault();
        }
    });
});
