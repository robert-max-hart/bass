// ----------------------------------------------------------------
// BUTTON
//     A clickable button inside the canvnas object
// ----------------------------------------------------------------
var BUTTON = (function () {
    const BUTTON_COLOR_1    = "#8888DD";
    const BUTTON_COLOR_2    = "#6666BB";

    const HOVER_COLOR_1     = "#AAAAFF";
    const HOVER_COLOR_2     = "#8888DD";

    const PRESS_COLOR_1     = "#7777AA";
    const PRESS_COLOR_2     = "#555588";

    const BUTTON_TEXT_COLOR = "#EEEEFF";
    const BUTTON_DISABLED   = "#99888888";
    const BUTTON_FONT       = "32px Classic Console";
    const TEXT_Y_OFFSET     = 25;

    const BS_DEFAULT        = 1;
    const BS_HOVER          = 2;
    const BS_PRESSED        = 3;
    const BS_DISABLED       = 4;

    // --------------------------------
    // Constructor
    // @param - x : X position of the button
    // @param - y : Y position of the button
    // @param - width : Width of the button
    // @param - height : Height of the button
    // @param - strLabel : String, text label the button will display
    // @param - fnCallback : Function to call when the button is pressed
    // @param - button_idx : ID to reference the button by
    // --------------------------------
    var but = function(x, y, width, height, strLabel="", fnCallback=null, button_idx=0)
    {
        this.rect = new RECT(x, y, width, height);
        this.strLabel = strLabel;
        this.strFont = BUTTON_FONT;
        this.fnCallback = fnCallback;
        this.idx = button_idx;
        this.iState = BS_DEFAULT;
        this.visible = false;

        this.GetX           = function()     { return this.rect.x; };
        this.GetY           = function()     { return this.rect.y; };
        this.SetX           = function(iX)   { this.rect.x = iX; };
        this.SetY           = function(iY)   { this.rect.y = iY; };
        this.SetPosition    = function(x, y) { this.rect.x = x; this.rect.y = y; };

        this.GetWidth = function()  { return this.rect.width; };
        this.GetHeight = function() { return this.rect.height; };

        this.GetLabel = function()          { return this.strLabel; };
        this.SetLabel = function(strLabel)  { this.strLabel = strLabel; };
        this.SetFont  = function(strFont)   { this.strFont = strFont; };

        this.GetFunction = function() { return this.fnCallback; };
        this.GetIdx      = function() { return this.idx; };

        this.GetState = function() { return this.iState; };
        this.Default  = function()
        {
            if (this.iState != BS_DEFAULT) { this.iState = BS_DEFAULT; return true; }
            else { return false; }
        };
        this.Hover = function()
        {
            if (this.iState != BS_HOVER && this.iState != BS_DISABLED) { this.iState = BS_HOVER; return true; }
            else { return false; }
        };
        this.Press= function()
        {
            if (this.iState != BS_PRESSED) { this.iState = BS_PRESSED; return true; }
            else { return false; }
        };
        this.Disable = function()
        {
            this.iState = BS_DISABLED;
            return true;
        };

    // --------------------------------
    // Use
    //     Runs the function attached to this button
    // --------------------------------
    this.Use = function()
    {
        if (this.iState != BS_DISABLED && this.fnCallback != null && typeof this.fnCallback === 'function')
        {
            this.fnCallback(this);
        }
    }

    // --------------------------------
    // CheckPoint
    //     Checks if the given x,y coordinates are inside the BUTTON
    // --------------------------------
    this.CheckPoint = function(x, y) { return this.rect.CheckPoint(x, y); };

    // --------------------------------
    // GetButtonColor
    //     Returns the appropriate color for the button
    // --------------------------------
    this.GetButtonColor = function()
    {
        let arrColor;
        switch (this.iState)
        {
            case BS_HOVER:   { arrColor = [HOVER_COLOR_1, HOVER_COLOR_2]; break; }
            case BS_PRESSED: { arrColor = [PRESS_COLOR_1, PRESS_COLOR_2]; break; }
            case BS_DISABLED:
            case BS_DEFAULT:
            default:         { arrColor = [BUTTON_COLOR_1, BUTTON_COLOR_2]; break; }
        } // end of switch

        return arrColor;
    };

    // --------------------------------
    // Draw
    //     Draws the button on the canvas
    // --------------------------------
    this.Draw = function(ctx)
    {
        if (ctx != null && this.visible == true)
        {
            let arrColor = this.GetButtonColor();
            const gradient = ctx.createLinearGradient(0, this.rect.y, 0, this.rect.y+this.rect.height);
            gradient.addColorStop(0, arrColor[0]); // Start
            gradient.addColorStop(1, arrColor[1]);  // End

            RENDER.DrawRoundedBox(this.rect.toArray(), gradient);
            ctx.font = this.strFont;
            ctx.textAlign = "center";
            ctx.fillStyle = (this.iState == BS_DISABLED) ? BUTTON_DISABLED : BUTTON_TEXT_COLOR;
            ctx.fillText(this.strLabel, this.rect.x + this.rect.width/2, this.rect.y+this.rect.height-TEXT_Y_OFFSET);
            ctx.textAlign = "left"; // reset text align
        }
    };
  }; // end of class

  return but;
}());
