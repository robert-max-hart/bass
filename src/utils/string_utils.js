// ----------------------------------------------------------------
// STRINGUTILS
//     A collection of utility functions related to parsing strings
// ----------------------------------------------------------------
var STRINGUTILS = (function () {
    var s = {}; // main

    // --------------------------------
    // GetLines
    //     Gets the number pf lines of text a given string would take up
    // @param - text : The text string to check
    // @param - iMaxWidth : The width of the box to check against
    // --------------------------------
    s.GetLines = function(text, iMaxWidth)
    {
        let arrLines = [];
        let ctx = GetCanvas();
        if (ctx == null) { return arrLines; }

        let arrWords = text.split(" ");
        let arrCurrentLine = arrWords[0];

        let strWord;
        let iWordsLength = (arrWords != null) ? arrWords.length : 0;
        let idx;
        let iWidth;
        for (idx = 1; idx < iWordsLength; ++idx)
        {
            strWord = arrWords[idx];
            iWidth = ctx.measureText(arrCurrentLine + " " + strWord).width;
            if (iWidth < iMaxWidth)
            {
                arrCurrentLine += " " + strWord;
            }
            else
            {
                arrLines.push(arrCurrentLine);
                arrCurrentLine = strWord;
            }
        }

        arrLines.push(arrCurrentLine);
        return arrLines;
    };

    return s;
}()); // end of class
