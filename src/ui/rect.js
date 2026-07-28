// ----------------------------------------------------------------
// RECT
//     Helper class to define a rectangle
// ----------------------------------------------------------------
var RECT = (function () {
  var rect = function(x, y, width, height)
  {
    this.x = x;
    this.y = y;
    this.width = width;
    this.height = height;

    // --------------------------------
    // CheckPoint
    //     Checks if the given x,y coordinates are inside the RECT
    // --------------------------------
    this.CheckPoint = function(x, y)
    {
        return (x >= this.x && y >= this.y && x <= this.x+this.width && y <= this.y+this.height);
    };

    // --------------------------------
    // toArray
    //      Converts the RECT into a four-element array
    // --------------------------------
    this.toArray = function()
    {
        return [this.x, this.y, this.width, this.height];
    }
  }


  return rect;
}()); // end of class
