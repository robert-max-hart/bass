// ----------------------------------------------------------------
// MOUSE
//     Handles mouse logic: clicking, moving, etc
// ----------------------------------------------------------------
var MOUSE = (function () {
    // const
    const VIEW_AREA = { left:0, top:0, right:1170, bottom:540 };
    // function
    var mouse = {};

    var m_arrButtons = [];
    var m_arrPosition = [];

    // --------------------------------
    // IsInBox
    //     Returns if the mouse is inside the given point
    // @param - x1 : Left-top corner of the bounding box
    // @param - y1 : Left-top corner of the bounding box
    // @param - x2 : Right-bottom corner of the bounding box
    // @param - y2 : Right-bottom corner of the bounding box
    // @param - bIncludeBounds : Boolean, if true then include the edges of the boundingg box
    // --------------------------------
    mouse.IsInBox = function(x1, y1, x2, y2, bIncludeBounds = false)
    {
        if (bIncludeBounds)
        {
            return (m_arrPosition[0] >= x1 &&
                    m_arrPosition[0] <= x2 &&
                    m_arrPosition[1] >= y1 &&
                    m_arrPosition[1] <= y2);
        }

        return (m_arrPosition[0] > x1 &&
                m_arrPosition[0] < x2 &&
                m_arrPosition[1] > y1 &&
                m_arrPosition[1] < y2);
    };

    // --------------------------------
    // IsInView
    //     Returns true if the mouse is inside the current canvas
    // --------------------------------
    mouse.IsInView = function()
    {
        return mouse.IsInBox(VIEW_AREA.left, VIEW_AREA.top, VIEW_AREA.right, VIEW_AREA.bottom);
    };

    // --------------------------------
    // Move
    //     Called when the mouse move event listener is triggered
    // @param - evt : The mouse event passed in from the event listener
    // --------------------------------
    mouse.Move = function(evt)
    {
        m_arrPosition = mouse.GetMousePosition(evt);
        if (mouse.IsInView())
        {
            let idx;
            let iLength = m_arrButtons.length;
            let cButton;
            let arrBounds;
            for (idx = 0; idx < iLength; ++idx)
            {
                cButton = m_arrButtons[idx];
                if (cButton != null && cButton.visible == true)
                {
                    if (cButton.CheckPoint(m_arrPosition[0], m_arrPosition[1])) { cButton.Hover(); }
                    else { cButton.Default(); }
                }
            } // end of for loop
        } // end main vs side view check
    };

    // --------------------------------
    // LeftClick
    //     Handles left mouse clicks and screen taps
    // @param - evt : The mouse event passed in from the event listener
    // --------------------------------
    mouse.LeftClick = function(evt)
    {
        m_arrPosition = mouse.GetMousePosition(evt);
        if (mouse.IsInView())
        {
            let cButton = mouse.GetSelectedButton();
            if (cButton != null && cButton.visible == true && typeof cButton.Use === "function")
            {
                cButton.Use();
            }
            else
            {
                DIALOG.OnMouseClick();
            }
        } // end main vs side view check
    };

    // --------------------------------
    // GetMousePosition
    //     Returns the current mouse position as an array [x, y]
    // @param - evt : The mouse event passed in from the event listener
    // --------------------------------
    mouse.GetMousePosition = function(evt)
    {
        let rect = myGameArea.canvas.getBoundingClientRect();
        return [ evt.clientX - rect.left, evt.clientY - rect.top ];
    };

    // --------------------------------
    // AddButton
    //     Adds a button to the screen for the mouse to check against clicks for
    // @param - button : A BUTTON object to be added
    // --------------------------------
    mouse.AddButton = function(button)
    {
        if (m_arrButtons.includes(button) == false)
        {
            m_arrButtons.push(button);
        }
    };

    // --------------------------------
    // ClearButtons
    //     Clears the button cache
    // --------------------------------
    mouse.ClearButtons = function()
    {
        let cButton;
        while (m_arrButtons.length > 0)
        {
          cButton = m_arrButtons.pop();
          delete cButton;
        }
    };

    // --------------------------------
    // GetSelectedButton
    //     Returns any button if it is currently hovered over
    // --------------------------------
    mouse.GetSelectedButton = function()
    {
        let idx;
        let iLength = m_arrButtons.length;
        let cButton;
        let arrBounds;
        for (idx = 0; idx < iLength; ++idx)
        {
            cButton = m_arrButtons[idx];
            if (cButton != null && cButton.CheckPoint(m_arrPosition[0], m_arrPosition[1]))
            {
                return cButton;
            }
        } // end of for loop

        return null;
    };

    return mouse;
}()); // end of class
