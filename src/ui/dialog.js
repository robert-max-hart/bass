// ----------------------------------------------------------------
// DIALOG
//     Handles the drawing and logic of showing dialogs, which include the text box, name box, and choice buttons
// ----------------------------------------------------------------
var DIALOG = (function () {
    // consts
    const DEFAULT_BOX = [64, 404, 1042, 128];
    const DEFAULT_FONT = "24px Verdana";
    const BOX_X_PADDING = 16;
    const BOX_Y_PADDING = 32;
    const TEXT_COLOR = "#EEEEFF";

    const NAME_BOX = [80, 372, 256, 32];
    const NAME_X_OFFSET = 16;
    const NAME_Y_OFFSET = 24;
    const NAME_FONT = "32px serif";

    const CURSOR_POSITION = [1064, 492];
    const CURSOR_URL = "./res/cursor_down.png";

    const TEXT_GRADIENT_TOP = "#2288DDCC";
    const TEXT_GRADIENT_BOTTOM = "#0033AACC";
    const NAME_GRADIENT_BOTTOM = "#44AAEE";
    const NAME_GRADIENT_TOP = "#88CCFF"

    const BUTTON_SPACING = 16;
    const BUTTON_WIDTH = 160;
    const BUTTON_HEIGHT = 64;
    const BUTTON_Y_WITH_DIALOG = 308;
    const BUTTON_Y_NO_DIALOG = 468;
    const CHARS_PER_SECOND = 60; // reference: 1 char per ~60fps frame
    // private vars
    var m_strDialog = "";
    var m_strName = "";
    var m_bItalics = false;
    var m_bBold = false;
    var m_iTextIdx = 0;
    var m_fCharAccumulator = 0;
    var m_bDialogShowing = false;
    var m_bDialogDone = false;
    var m_arrChoiceData = null;
    var m_cursor;
    var m_bCursorLoaded = false;
    var m_objFontHeightCache = {};

    var m_arrChoices = [];
    // main
    var d = {};

    // --------------------------------
    // constructor logic
    // --------------------------------
    m_cursor = new Image();
    m_cursor.onload = function () { m_bCursorLoaded = true; };
    m_cursor.src = CURSOR_URL;

    // --------------------------------
    // Init
    //     Initialization logic for setting things up that needs to run after the canvas has been created
    // --------------------------------
    d.Init = function()
    {
        var choice_button;
        for (var idx = 0; idx < 4; ++idx)
        {
            choice_button = new BUTTON(0, 0, BUTTON_WIDTH, BUTTON_HEIGHT, "CHOICE", DIALOG.OnChoiceClick);
            MOUSE.AddButton(choice_button);
            m_arrChoices.push(choice_button);
        }
    };

    // --------------------------------
    // SetText
    //     Sets the text and other data for the dialog
    // @param - strText : String of the text to display in the text box
    // @param - strName : String of the text to display in the name box
    // @param - bUseItalics : Boolean, if true format the text box to be italic
    // @param - bUseBold : Boolean, if true format the text box to be bold
    // @param - arChoice : An array of possible choices to present to the player
    // --------------------------------
    d.SetText = function(strText, strName="", bUseItalics=false, bUseBold=false, arrChoice=null)
    {
        m_strDialog = strText;
        m_strName = strName;
        m_bItalics = bUseItalics;
        m_bBold = bUseBold;
        m_arrChoiceData = arrChoice;
        m_iTextIdx = 0;
        m_fCharAccumulator = 0;
        m_bDialogShowing = true;
        m_bDialogDone = false;

        for (const choice_button of m_arrChoices) { if (choice_button != null) { choice_button.visible = false; } }

        if (m_arrChoiceData != null && m_arrChoiceData.length > 0)
        {
            let iLength = m_arrChoiceData.length;
            let iStartingX = GetCanvasWidth()/2 - ( (BUTTON_WIDTH - BUTTON_SPACING) * (iLength/2));
            let iYposition = (m_strDialog != "") ? BUTTON_Y_WITH_DIALOG : BUTTON_Y_NO_DIALOG;
            let idx;
            let button_choice;
            let objChoice;
            for (idx = 0; idx < iLength; ++idx)
            {
                button_choice = m_arrChoices[idx];
                objChoice = (idx < m_arrChoiceData.length) ? m_arrChoiceData[idx] : null;
                if (button_choice != null && objChoice != null)
                {
                    button_choice.SetLabel(objChoice.text);
                    button_choice.SetPosition(iStartingX, iYposition);
                    iStartingX += (BUTTON_WIDTH + BUTTON_SPACING);
                }
            }
        }
    };

    // --------------------------------
    // ClearText
    //     Clears all the dialog data
    // --------------------------------
    d.ClearText = function()
    {
        m_strDialog = "";
        m_strName = "";
        m_bItalics = false;
        m_bBold = false;
        m_arrChoiceData = [];
        m_iTextIdx = 0;
        m_fCharAccumulator = 0;
        m_bDialogShowing = false;
        m_bDialogDone = true;

        for (const choice_button of m_arrChoices) { if (choice_button != null) { choice_button.visible = false; } }
    };

    // --------------------------------
    // DrawDialog
    //     Draws all of the dialog components to the screen
    // --------------------------------
    d.DrawDialog = function(fDeltaMs=1000/60)
    {
        let ctx = GetCanvas();
        if (ctx != null && m_bDialogShowing)
        {
            const gradient = ctx.createLinearGradient(0, DEFAULT_BOX[1], 0, DEFAULT_BOX[1]+DEFAULT_BOX[3]);
            gradient.addColorStop(0, TEXT_GRADIENT_TOP); // Start
            gradient.addColorStop(1, TEXT_GRADIENT_BOTTOM);  // End

            if (m_strDialog != "") { RENDER.DrawRoundedBox(DEFAULT_BOX, gradient); }
            if (m_strName != "")   { DIALOG.DrawName(); }
            DIALOG.DrawText(fDeltaMs);

            for (const choice_button of m_arrChoices)
            {
                if (choice_button != null) { choice_button.Draw(ctx); }
            }
        }
    };

    // --------------------------------
    // DrawName
    //     Draws the name box to the screen
    // --------------------------------
    d.DrawName = function()
    {
        let ctx = GetCanvas();
        if (ctx == null) { return; }

        const name_grad = ctx.createLinearGradient(0, NAME_BOX[1], 0, NAME_BOX[1]+NAME_BOX[3]);
        name_grad.addColorStop(0, NAME_GRADIENT_TOP); // Start
        name_grad.addColorStop(1, NAME_GRADIENT_BOTTOM);  // End
        RENDER.DrawRoundedBox(NAME_BOX, name_grad);
        ctx.font = DEFAULT_FONT;
        ctx.fillStyle = TEXT_COLOR;
        RENDER.EnableShadow();
        ctx.fillText(m_strName, NAME_BOX[0]+NAME_X_OFFSET, NAME_BOX[1]+NAME_Y_OFFSET);
        RENDER.DisableShadow();
    };

    // --------------------------------
    // DrawText
    //     Draws the text in the text box using an animation function to simulate a typewriter effect
    // --------------------------------
    d.DrawText = function(fDeltaMs=1000/60)
    {
        let ctx = GetCanvas();
        if (ctx == null) { return; }

        ctx.font = DIALOG.GetFont();
        ctx.fillStyle = TEXT_COLOR;
        RENDER.EnableShadow();
        let strText;
        let idx;
        let bTextDone = m_iTextIdx >= m_strDialog.length;
        if (bTextDone)
        {
            m_bDialogDone = true;
            strText = m_strDialog;
            if (m_arrChoiceData != null)
            {
                let iLength = m_arrChoiceData.length;
                let choice_button;
                for (idx=0; idx < iLength; ++idx)
                {
                    choice_button = (idx < m_arrChoices.length) ? m_arrChoices[idx] : null;
                    if (choice_button != null) { choice_button.visible = true; }
                }
            }
            else if (m_bCursorLoaded)
            {
                ctx.drawImage(m_cursor, CURSOR_POSITION[0], CURSOR_POSITION[1]);
            }
        }
        else
        {
            strText = m_strDialog.substring(0, m_iTextIdx);
            m_fCharAccumulator += (CHARS_PER_SECOND * fDeltaMs) / 1000;
            while (m_fCharAccumulator >= 1 && m_iTextIdx < m_strDialog.length)
            {
                m_iTextIdx += 1;
                m_fCharAccumulator -= 1;
            }
        }

        let arrLines = STRINGUTILS.GetLines(strText, DEFAULT_BOX[2]-32);
        let iTextHeight = DIALOG.GetFontHeight(ctx);
        let iLines = arrLines.length;
        for (idx = 0; idx < iLines; ++idx)
        {
            ctx.fillText(arrLines[idx], DEFAULT_BOX[0]+BOX_X_PADDING, DEFAULT_BOX[1]+BOX_Y_PADDING + (iTextHeight * idx));
        }

        RENDER.DisableShadow();
    };

    // --------------------------------
    // GetFontHeight
    //     Returns the line height for the current ctx.font, cached per font string
    //     since font metrics don't depend on the text content being measured
    // --------------------------------
    d.GetFontHeight = function(ctx)
    {
        let strFont = ctx.font;
        if (m_objFontHeightCache[strFont] == null)
        {
            let textMetrics = ctx.measureText("Mgjy"); // sample text with ascenders/descenders to measure line height
            m_objFontHeightCache[strFont] = textMetrics.fontBoundingBoxAscent + textMetrics.fontBoundingBoxDescent;
        }

        return m_objFontHeightCache[strFont];
    };

    // --------------------------------
    // GetFont
    //     Gets the currently set font for the text box and adds any markup
    // --------------------------------
    d.GetFont = function()
    {
        let strFont = "";
        if (m_bItalics) { strFont += "italic "; }
        if (m_bBold)    { strFont += "bold "; }
        return strFont + DEFAULT_FONT;
    };

    // --------------------------------
    // OnChoiceClick
    //     Called when a choice button is clicked
    // --------------------------------
    d.OnChoiceClick = function(button)
    {
        if (m_arrChoiceData != null)
        {
            let idx = m_arrChoices.indexOf(button);
            if (idx >= 0)
            {
                let objChoice = (idx < m_arrChoiceData.length) ? m_arrChoiceData[idx] : null;
                if (objChoice != null && objChoice.target != null)
                {
                    EVENTS.SetNextEventIdx(objChoice.target);
                    EVENTS.ParseNextEvent(); // for now just keep going
                }
            }
        }
    };

    // --------------------------------
    // OnMouseClick
    //     When the mouse is clicked
    // --------------------------------
    d.OnMouseClick = function()
    {
        if (m_bDialogShowing)
        {
            if (m_bDialogDone && (m_arrChoiceData == null || m_arrChoiceData.length < 1))
            {
                m_bDialogShowing = false;
                EVENTS.ParseNextEvent();
            }
            else
            {
                m_iTextIdx = m_strDialog.length; // fast forward text
            }
        }
    };

    return d;
}()); // end of class
