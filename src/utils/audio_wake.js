// ----------------------------------------------------------------
// AUDIO_WAKE
//     Keeps the sound output awake so the start of a sound isn't cut off.
//     Idle outputs power down (PipeWire suspends the sound card after ~5 s, the Intel HDA chip powers off
//     after 1 s, Bluetooth and USB devices do the same) and lose the first part of the next sound while
//     they wake up. A near-silent stream that runs while the page is open keeps them on. Browsers allow
//     audio only after a click or key press, so the stream starts on the first one; a sound played in the
//     first moments after that waits until the output is up.
// ----------------------------------------------------------------
var AUDIO_WAKE = (function () {
    // consts
    const WAKE_MS = 500;            // how long an idle output may take to come back
    const LEVEL = 1 / 32768;        // one 16-bit step: inaudible, but not digital silence
    // private vars
    var m_ctx = null;
    var m_fStartMs = 0;
    // main
    var w = {};

    // --------------------------------
    // Start
    //     Starts (or resumes) the near-silent stream; called on every click and key press
    // --------------------------------
    function Start()
    {
        try
        {
            if (m_ctx == null)
            {
                const Ctx = window.AudioContext || window.webkitAudioContext;
                if (Ctx == null) { return; }
                m_ctx = new Ctx();
                const src = m_ctx.createConstantSource();
                src.offset.value = LEVEL;
                src.connect(m_ctx.destination);
                src.start();
                m_fStartMs = performance.now();
            }
            if (m_ctx.state === "suspended")
            {
                m_ctx.resume().catch(function() {});
                m_fStartMs = performance.now();
            }
        }
        catch (err) { console.log("Audio wake stream did not start (" + err.name + ")"); }
    }

    // --------------------------------
    // Play
    //     Plays an Audio object, waiting for the output first if the wake stream has only just started
    // @param - audio : the Audio object
    // @param - fnError : called with the error if the browser refuses to play it
    // --------------------------------
    w.Play = function(audio, fnError)
    {
        w.Cancel(audio);
        const fnGo = function()
        {
            audio.wakeTimer = null;
            let promise = audio.play();
            if (promise != null && fnError != null) { promise.catch(fnError); }
        };
        const fWaitMs = (m_ctx == null) ? 0 : m_fStartMs + WAKE_MS - performance.now();
        if (fWaitMs > 0) { audio.wakeTimer = setTimeout(fnGo, fWaitMs); }
        else             { fnGo(); }
    };

    // --------------------------------
    // Cancel
    //     Cancels a play that is still waiting for the output
    // --------------------------------
    w.Cancel = function(audio)
    {
        if (audio != null && audio.wakeTimer) { clearTimeout(audio.wakeTimer); audio.wakeTimer = null; }
    };

    // --------------------------------
    // Stop
    //     Cancels a waiting play and pauses the Audio object
    // --------------------------------
    w.Stop = function(audio)
    {
        if (audio == null) { return; }
        w.Cancel(audio);
        audio.pause();
    };

    // --------------------------------
    // constructor logic
    // --------------------------------
    for (const strEvent of ["pointerdown", "keydown"]) { document.addEventListener(strEvent, Start, { capture: true }); }

    return w;
}());
