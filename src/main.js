// ----------------------------------------------------------------
// main.js
//     The main JS file loaded on start
// ----------------------------------------------------------------
// consts
const CANVAS_WIDTH = 1170;
const CANVAS_HEIGHT = 540;
const PREVIEW_WIDTH = 585;
const PREVIEW_HEIGHT = 270;
const LOGO_URL = "./res/logo_screen.png";
const FIN_URL = "./res/fin_screen.png";

var m_bEditMode = false;

var myGameArea =
{
    canvas : document.createElement("canvas"),
    bListenersAttached : false,
    start  : function(canvas_target)
    {
        this.canvas.width = (m_bEditMode) ? PREVIEW_WIDTH : CANVAS_WIDTH;
        this.canvas.height = (m_bEditMode) ? PREVIEW_HEIGHT : CANVAS_HEIGHT;
        this.context = this.canvas.getContext("2d");
        document.getElementById(canvas_target).appendChild(this.canvas);
        if (this.bListenersAttached == false)
        {
            this.canvas.addEventListener("mousemove", MOUSE.Move);
            this.canvas.addEventListener("click", MOUSE.LeftClick);
            this.bListenersAttached = true;
        }
        RENDER.DrawImage(LOGO_URL);
    },
    clear : function(strFill=null)
    {
        this.context.clearRect(0, 0, this.canvas.width, this.canvas.height);
        if (strFill != null)
        {
            this.context.fillStyle = strFill;
            this.context.fillRect(0, 0, this.canvas.width, this.canvas.height);
        }
    }
};

// --------------------------------
// GetCanvas
//     Returns the canvas object
// --------------------------------
function GetCanvas() { return myGameArea.context; }

// --------------------------------
// GetCanvasWidth
// GetCanvasHeight
//     Returns how wide / tall the canvas object is
// --------------------------------
function GetCanvasWidth()  { return myGameArea.canvas.width;  }
function GetCanvasHeight() { return myGameArea.canvas.height; }

// --------------------------------
// InEditMode
//     Returns if we are in edit mode or player mode
// --------------------------------
function InEditMode() { return m_bEditMode; }
function SetEditMode(bEdit) { m_bEditMode = bEdit; }

// --------------------------------
// Update
//     Main update loop for animating
// --------------------------------
var m_fLastTimestamp = null;

function Update(timestamp)
{
    if (typeof timestamp !== "number") { timestamp = performance.now(); }
    let fDeltaMs = (m_fLastTimestamp != null) ? (timestamp - m_fLastTimestamp) : (1000/60);
    m_fLastTimestamp = timestamp;

    RENDER.Render(fDeltaMs);
    requestAnimationFrame(Update);
}

// --------------------------------
// OnStart
//     Called when the page loads
// --------------------------------
function OnStart()
{
    EDIT.Populate();
}

// --------------------------------
// LoadFile
//     Loads a story file for editing or playing
// --------------------------------
function LoadFile(bEditMode = false)
{
    m_bEditMode = bEditMode;
    let load_file = document.getElementById("load_file");
    if (load_file != null && load_file.value != null)
    {
        EVENTS.FetchEventsFile(load_file.value);
    }
}

// --------------------------------
// LoadComplete
//     Callback after the story file has been loaded
// --------------------------------
function LoadComplete()
{
    let canvas_target = (m_bEditMode) ? "divCanvasEdit" : "divCanvas";
    myGameArea.start(canvas_target);
    DIALOG.Init();

    let s_load_div = document.getElementById("divLoad");
    if (s_load_div != null) { s_load_div.style.display = "none"; }
    let s_logo = document.getElementById("logo");
    if (s_logo != null) { s_logo.parentNode.removeChild(s_logo); }

    if (m_bEditMode)
    {
        let s_file_div = document.getElementById("divFile");
        let s_edit_div = document.getElementById("divEdit");

        if (s_file_div != null) { s_file_div.style.display = "block"; }
        if (s_edit_div != null) { s_edit_div.style.display = "flex";  }
    }
    else
    {
        Update();
    }
}

// --------------------------------
// LoadFailed
//     xcallback after a failed file load
// --------------------------------
function LoadFailed(err)
{
    window.alert("ERROR loading file : " + err);
}

// --------------------------------
// ExitEditMode
//     Exits the editing mode and runs whatever is passed
// @param - data : Events data to run
// --------------------------------
function ExitEditMode(data)
{
    let s_file_div = document.getElementById("divFile");
    let s_edit_div = document.getElementById("divEdit");

    if (s_file_div != null) { s_file_div.style.display = "none"; }
    if (s_edit_div != null) { s_edit_div.style.display = "none";  }

    m_bEditMode = false;
    RENDER.Clear();
    myGameArea.start("divCanvas");
    Update();
    EVENTS.SetEventsData(data);
}

// end of main
