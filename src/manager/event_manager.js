// ----------------------------------------------------------------
// EVENTS
//     Event manager for all of the events in a story
// ----------------------------------------------------------------
var EVENTS = (function () {
    // consts
    const DEFAULT_WAIT = 1000;
    // main
    var events = {};

    var m_file_name;
    var m_events_data;
    var m_story_name = "";
    var m_title_data;
    var m_chapter_list = {};
    var m_current_chapter = "0";
    var m_event_list = {};
    var m_event_idx = 0;
    var m_iCheckpoint = -1;

    // --------------------------------
    // FetchEventsFile
    //     Reads in a story file from a remote location
    // @param - strPath : String URL (file and path) of the file to read
    // --------------------------------
    events.FetchEventsFile = function(strPath)
    {
        let arrPathSplit = strPath.split("/");
        m_file_name = arrPathSplit.pop();
        fetch(strPath)
            .then(response =>
            {
                if (!response.ok) { throw new Error(`HTTP error! Status: ${response.status}`); }
                return response.json();
            })
            .then(data => EVENTS.SetEventsData(data))
            .catch(error => LoadFailed(error));
    };

    // --------------------------------
    // SetEventsData
    //     Callback to set the story and events data after loading it
    // @param - data - A data object parsed from a JSON string that is all story events and metadata
    // --------------------------------
    events.SetEventsData = function(data)
    {
        m_events_data = data;
        if (m_events_data != null && "chapters" in m_events_data)
        {
            m_chapter_list = m_events_data.chapters;
            EVENTS.SetChapter(m_current_chapter);
            LoadComplete();
            if (InEditMode()) { EDIT.EditFile(); }
            else              { EVENTS.ShowTitle(); }
        }
    };

    // --------------------------------
    // ClearEventsData
    //     Clears out the current story in memory
    // --------------------------------
    events.ClearEventsData = function()
    {
        m_file_name = ""
        m_events_data = null;
        m_story_name = "";
        m_title_data = null;
        m_chapter_list = {};
        m_current_chapter = "0";
        m_event_list = {};
        m_event_idx = 0;
    };

    // --------------------------------
    // Getters and Setters
    //     Various get and set functions for the data
    // --------------------------------
    events.GetFileName = function() { return m_file_name; };

    events.GetEventsData     = function()        { return m_events_data; };
    events.GetChapters       = function()        { return m_chapter_list; };
    events.GetCurrentChapter = function()        { return m_current_chapter; };
    events.GetChapterName    = function()        { return (m_chapter_list[m_current_chapter] != null) ? m_chapter_list[m_current_chapter]["label"] : ""; };
    events.SetChapterName    = function(strName) { if (m_chapter_list[m_current_chapter] != null) { m_chapter_list[m_current_chapter]["label"] = strName; } };
    events.GetEventsList     = function()        { return m_event_list; };
    events.GetEventByID      = function(idx)     { return m_event_list[idx]; }

    events.GetStoryName = function()        { return (m_events_data != null) ? m_events_data.story_name : ""; }
    events.SetStoryName = function(strName) { m_events_data.story_name = strName; };
    events.GetTitleScreen = function()          { return (m_events_data != null && m_events_data.title_data != null) ? m_events_data.title_data.title_screen : ""; };
    events.SetTitleScreen = function(strURL)    { m_events_data.title_data = {"title_screen":strURL}; };

    // --------------------------------
    // SetNextEventIdx
    //     Sets the ID of the next event
    // --------------------------------
    events.SetNextEventIdx = function(idx)
    {
        m_event_idx = idx;
    };

    // --------------------------------
    // ParseNextEvent
    //     Parses through the next event object and fires off the different functions for each flag
    // --------------------------------
    events.ParseNextEvent = function()
    {
        if (m_event_list[m_event_idx] != null)
        {
            let next_event = m_event_list[m_event_idx]
            if (next_event == null) { return; }

            if ("chapter" in next_event) { EVENTS.SetChapter(next_event.chapter); }
            if ("next" in next_event)    { m_event_idx = next_event.next; }

            if (next_event.fadein == true)       { RENDER.SetFade(0.0); }
            else if (next_event.fadeout == true) { RENDER.SetFade(1.0, true); }

            if (next_event["background_clear"] == true) { RENDER.ClearBackground(); }
            else if ("background" in next_event)        { RENDER.SetBackground(next_event.background); }

            if (next_event["foreground_clear"] == true) { RENDER.ClearForeground(); }
            else if ("foreground" in next_event)        { RENDER.SetForeground(next_event.foreground); }

            if ("dialog" in next_event)
            {
                DIALOG.SetText(next_event.dialog, next_event.name, next_event.italics, next_event.bold, next_event.choice);
            }
            else
            {
                DIALOG.ClearText();
                EVENTS.AutoAdvance(next_event.wait);
            }
        }
        else
        {
            console.log("No next event - assuming this is the end of the story");
            RENDER.Clear();
            RENDER.SetBackground(FIN_URL);
        }
    };

    // --------------------------------
    // SetChapter
    //     Sets the currently-read chapter
    // @param - idx : ID key of the chapter to load
    // --------------------------------
    events.SetChapter = function(idx)
    {
        let strKey = idx.toString();
        if (m_chapter_list != null && m_chapter_list[strKey] != null)
        {
            m_current_chapter = strKey;
            let chapter = m_chapter_list[m_current_chapter];
            if (chapter != null) { m_event_list = chapter.events; }
        }
    };

    // --------------------------------
    // ShowTitle
    //     Shows the title screen, if it exists
    // --------------------------------
    events.ShowTitle = function()
    {
        let title_data = (m_events_data != null) ? m_events_data["title_data"] : null;
        if (title_data != null)
        {
            RENDER.SetFade(0.0);
            if (title_data.title_screen != null) { RENDER.SetBackground(title_data.title_screen); }
            m_event_list["-1"] = { "fadeout":true, "next":0 };
            // if we have save data use that to populate m_iCheckpoint
            m_iCheckpoint = -1;
            let arrTitleData = [ {"text":"New Game", "target":-1}, {"text":"Load Game", "target":m_iCheckpoint } ];
            DIALOG.SetText("", "", false, false, arrTitleData);
        }
        else
        {
            EVENTS.ParseNextEvent();
        }
    };

    // --------------------------------
    // AutoAdvance
    //     Automatically advance to the next event
    // @param - wait_time - Time in milleseconds to wait until advancing
    // --------------------------------
    events.AutoAdvance = async function(wait_time)
    {
        const x = await EVENTS.WaitOneSec(wait_time);
        EVENTS.ParseNextEvent();
    };

    // --------------------------------
    // WaitOneSec
    //     Waits one second
    // @param - wait_time : How long to actual wait in milleseconds
    // --------------------------------
    events.WaitOneSec = function(wait_time = DEFAULT_WAIT)
    {
        return new Promise((resolve) => { setTimeout(() => { resolve(); }, wait_time); });
    };

    // ----------------------------------------------------------------
    // Editing functions
    // ----------------------------------------------------------------
    // AddChapter
    //     Adds a blank chapter to the events data
    // @param - idx : ID key of the new chapter
    // --------------------------------
    events.AddChapter = function(idx)
    {
        if (m_chapter_list != null && idx in m_chapter_list == false)
        {
            m_chapter_list[idx] = { "events": {"0":{ "next":1 } } };
        }
    };

    // --------------------------------
    // AddEvent
    //     Adds a new event to the end of the current chapter
    // @param - idx : ID key of the new event
    // --------------------------------
    events.AddEvent = function(idx)
    {
        if (m_event_list != null && idx >= 0 && idx in m_event_list == false)
        {
            m_event_list[idx] = {"next":(idx+1)};
        }
    };

    // --------------------------------
    // InsertEvent
    //     Adds a new event to the current chapter in the middle of the chapter
    // @param - iInsertAtIdx : ID key where the event should be inserted after
    // --------------------------------
    events.InsertEvent = function(iInsertAtIdx)
    {
        if (m_event_list != null)
        {
            let iNewIdx = iInsertAtIdx +1;
            let list_copy = Object.assign({}, m_event_list); // copy object
            // first move everything up
            let idx;
            let iNextEvent;
            for (let [key, item] of Object.entries(list_copy))
            {
                if (item != null && item.next != null)
                {
                    iNextEvent = parseInt(item.next, 10);
                    if (iNextEvent > iNewIdx)
                    {
                        item.next = (iNextEvent + 1);
                    }

                }

                idx = parseInt(key, 10);
                if (idx > iInsertAtIdx)
                {
                    m_event_list[(idx + 1).toString()] = item;
                }
            } // end for loop
            // now insert one at the index, defaulting its "next" to whatever now follows it
            m_event_list[iNewIdx.toString()] = { "next": (iNewIdx + 1) };
            return true;
        }

        return false;
    };

    // --------------------------------
    // NewEventFile
    //     Creates a new story file
    // --------------------------------
    events.NewEventFile = function()
    {
        let new_file = { "chapters":{} };
        new_file["chapters"]["0"] = { "events": {"0":{ "next":1 } } };
        EVENTS.SetEventsData(new_file);
    };

    // --------------------------------
    // SaveCurrentChapter
    //     Saves the current chapter data to the storry file
    // --------------------------------
    events.SaveCurrentChapter = function()
    {
        m_chapter_list[m_current_chapter] = m_event_list;
    };

    // --------------------------------
    // RemoveCurrentChapter
    //     Removes the current chapter from data
    // --------------------------------
    events.RemoveCurrentChapter = function()
    {
        if (m_chapter_list != null)
        {
            delete m_chapter_list[m_current_chapter];
            let list_copy = Object.assign({}, m_chapter_list);
            let idx;
            let iRemoveIdx = parseInt(m_current_chapter, 10);
            let iLargestIdx = 0;
            for (let [key, item] of Object.entries(list_copy))
            {
                idx = parseInt(key, 10);
                if (idx > iLargestIdx) { iLargestIdx = idx; }
                if (idx > iRemoveIdx)
                {
                    m_chapter_list[(idx - 1).toString()] = item;
                }
            } // end for loop
            delete m_chapter_list[iLargestIdx.toString()];
            return true;
        }

        return false;
    };

    // --------------------------------
    // SaveEvent
    //     Saves an event data to the event list
    // @param - idx : ID key of the event to save
    // @param - obj : Saved object data of the event
    // --------------------------------
    events.SaveEvent = function(idx, obj)
    {
        if (m_event_list != null && idx >= 0) { m_event_list[idx] = obj; }
    };

    // --------------------------------
    // RemoveEvent
    //     Removes a given event from the event list
    // @param - iRemoveIdx : ID key of the event to remove
    // --------------------------------
    events.RemoveEvent = function(iRemoveIdx)
    {
        if (m_event_list != null && iRemoveIdx >= 0)
        {
            delete m_event_list[iRemoveIdx];
            let list_copy = Object.assign({}, m_event_list);
            let idx;
            let iLargestIdx = 0;
            for (let [key, item] of Object.entries(list_copy))
            {
                if (item != null && item.next != null && item.next > iRemoveIdx)
                {
                    item.next -= 1;
                }

                idx = parseInt(key, 10);
                if (idx > iLargestIdx) { iLargestIdx = idx; }
                if (idx > iRemoveIdx)
                {
                    m_event_list[(idx - 1).toString()] = item;
                }
            } // end for loop
            delete m_event_list[iLargestIdx.toString()];
            return true;
        }

        return false;
    };

    return events;
}()); // end of class
