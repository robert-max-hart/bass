// ----------------------------------------------------------------
// EDIT
//     Manager file for handling all the editor commands
// ----------------------------------------------------------------
var EDIT = (function () {
    // consts
    const OUTPUT_FILE_DEFAULT = "new_story.json";
    // main
    var edit = {};

    var s_story_name;
    var s_title_screen;
    var s_export_file;

    var s_idx_label;
    var s_label_edit;
    var s_name_edit;
    var s_italics;
    var s_bold;
    var s_dialog_edit;

    var s_next_chapter;
    var s_next_event;
    var s_wait_time;
    var s_background_scene;
    var s_background_clear;
    var s_foreground_scene;
    var s_foreground_clear;
    var s_effects_scene;
    var s_effects_clear;
    var s_fade_in;
    var s_fade_out;

    var s_choice_a;
    var s_choice_b;
    var s_choice_c;
    var s_choice_d;
    var m_arrChoices;
    var s_save_event;

    var s_chapter_list;
    var s_event_list;

    var m_iChoices = 0;

    var m_iSelectedEventIdx = -1;
    var m_bPendingEventChange = false;

    // --------------------------------
    // AddChapter
    //     Adds a chapter
    // --------------------------------
    edit.AddChapter = function()
    {
        if (s_chapter_list != null)
        {
            let count = s_chapter_list.options.length;
            EVENTS.AddChapter(count);
            EDIT.GetChapters();
            s_chapter_list.selectedIndex = parseInt(count, 10);
            EDIT.ChangeChapter();
        }
    };

    // --------------------------------
    // AddEvent
    //     Adds an event to the end of the current chapter
    // --------------------------------
    edit.AddEvent = function()
    {
        if (s_event_list != null)
        {
            EDIT.SaveEvent();
            let count = s_event_list.options.length;
            EVENTS.AddEvent(count);
            EDIT.GetEvents(); // reload side bar
            s_event_list.selectedIndex = s_event_list.options.length - 1;
            s_event_list.focus();
            EDIT.EditEvent(false);
        }
    };

    // --------------------------------
    // ChangeChapter
    //     Changes which chapter we are editing
    // --------------------------------
    edit.ChangeChapter = function()
    {
        if (s_chapter_list != null)
        {
            EDIT.SaveEvent();
            let idx = s_chapter_list.selectedIndex;
            EVENTS.SetChapter(idx);
            EDIT.GetEvents();
            EDIT.EditEvent(false);
        }
    };

    // --------------------------------
    // DeleteChapter
    //     Removes the currently selected chapter from data
    // --------------------------------
    edit.DeleteChapter = function()
    {
        if (s_chapter_list != null && s_chapter_list.options.length <= 1)
        {
            window.alert("Cannot delete the only remaining chapter.");
            return;
        }

        let bConfirm = window.confirm("Are you sure you want to delete the chapter and all events within it?");
        if (bConfirm)
        {
            let idx = s_chapter_list.selectedIndex;
            if (idx > 0) { idx -= 1; }
            EVENTS.RemoveCurrentChapter();
            EVENTS.SetChapter(idx);
            EDIT.GetChapters(idx);
            EDIT.GetEvents();
            EDIT.EditEvent(false);
        }
    };

    // --------------------------------
    // EditEvent
    //     Sets up the editor to read in a selected event for editing
    // @param - bSaveEvent : Boolean, if true save the previously loaded event before editing a new one
    // --------------------------------
    edit.EditEvent = function(bSaveEvent=true)
    {
        if (s_event_list != null)
        {
            let idx = s_event_list.selectedIndex; // grab the idx first since SaveEvent changes it
            if (bSaveEvent == true)
            {
                EDIT.SaveEvent(); // save off old events
            }

            s_event_list.selectedIndex = m_iSelectedEventIdx = idx;
            let ev = EVENTS.GetEventByID(idx);
            if (ev != null)
            {
                // center panel
                if (s_idx_label != null)   { s_idx_label.innerHTML = idx; }
                if (s_label_edit != null)  { s_label_edit.value = ev["label"] != null ? ev["label"] : ""; }
                if (s_name_edit != null)   { s_name_edit.value = ev["name"] != null ? ev["name"] : ""; }
                if (s_italics != null)     { s_italics.checked = ev["italics"] != null ? ev["italics"] : false; }
                if (s_bold != null)        { s_bold.checked = ev["bold"] != null ? ev["bold"] : false; }
                if (s_dialog_edit != null) { s_dialog_edit.value = ev["dialog"] != null ? ev["dialog"] : ""; }
                // left side panel
                if (s_next_chapter != null)     { s_next_chapter.value = ev["chapter"] != null ? ev["chapter"] : ""; }
                if (s_next_event != null)       { s_next_event.value = ev["next"] != null ? ev["next"] : ""; }
                if (s_wait_time != null)        { s_wait_time.value = ev["wait"] != null ? ev["wait"] : ""; }

                if (s_background_scene != null) { s_background_scene.value = ev["background"] != null ? ev["background"] : ""; }
                if (s_background_clear != null) { s_background_clear.checked = ev["background_clear"] != null ? ev["background_clear"] : false; }
                EDIT.PreviewBackground();
                if (s_foreground_scene != null) { s_foreground_scene.value = ev["foreground"] != null ? ev["foreground"] : ""; }
                if (s_foreground_clear != null) { s_foreground_clear.checked = ev["foreground_clear"] != null ? ev["foreground_clear"] : false; }
                EDIT.PreviewForeground();
                if (s_effects_scene != null) { s_effects_scene.value = ev["effects"] != null ? ev["effects"] : ""; }
                if (s_effects_clear != null) { s_effects_clear.checked = ev["effects_clear"] != null ? ev["effects_clear"] : false; }
                EDIT.PreviewEffects();

                if (s_fade_in != null)          { s_fade_in.checked = ev["fadein"] != null ? ev["fadein"] : false; }
                if (s_fade_out != null)         { s_fade_out.checked = ev["fadeout"] != null ? ev["fadeout"] : false; }
                // choice panel
                if (m_arrChoices != null && m_arrChoices.length > 0)
                {
                    let arrEventChoices = ev["choice"];
                    let objChoice;
                    let divChoice;
                    let arrInputs;
                    let choice_text;
                    let arrNumbers;
                    let choice_target;
                    let iChoice;
                    let iLength = m_arrChoices.length;
                    for (iChoice = 0; iChoice < iLength; ++iChoice)
                    {
                        divChoice = m_arrChoices[iChoice];
                        if (divChoice != null)
                        {
                            objChoice = (arrEventChoices != null && iChoice < arrEventChoices.length) ? arrEventChoices[iChoice] : null;
                            arrInputs = divChoice.getElementsByClassName("choice_text");
                            choice_text = (arrInputs != null && arrInputs.length > 0) ? arrInputs[0] : null;
                            if (choice_text != null)
                            {
                                choice_text.value = (objChoice != null) ? objChoice.text : "";
                            }

                            arrNumbers = divChoice.getElementsByClassName("short");
                            choice_target = (arrNumbers != null && arrNumbers.length > 0) ? arrNumbers[0] : null;
                            if (choice_target != null)
                            {
                                choice_target.value = (objChoice != null) ? objChoice.target : "";
                            }
                        }
                    } // end for loop
                }

            } // end event data null check
        } // end s_event_list check
    };

    // --------------------------------
    // EditFile
    //     Loads the story file and events for editing
    // --------------------------------
    edit.EditFile = function()
    {
        if (s_story_name != null && EVENTS.GetStoryName() != null)
        {
            s_story_name.value = EVENTS.GetStoryName();
        }
        if (s_title_screen != null && EVENTS.GetTitleScreen() != null)
        {
            s_title_screen.value = EVENTS.GetTitleScreen();
            EDIT.PreviewTitle();
        }
        if (s_export_file != null && EVENTS.GetFileName() != null)
        {
            s_export_file.value = EVENTS.GetFileName();
        }

        EDIT.GetChapters();
        EDIT.GetEvents();
        EDIT.EditEvent(false);
    };

    // --------------------------------
    // FieldChanged
    //     Callback signal for when a field has changed
    // --------------------------------
    edit.FieldChanged = function()
    {
        EDIT.SaveEvent();
    };

    // --------------------------------
    // InsertEvent
    //     Adds an event to the middle of the chapter
    // --------------------------------
    edit.InsertEvent = function()
    {
        if (s_event_list != null)
        {
            EDIT.SaveEvent();
            let idx = s_event_list.selectedIndex;
            let bSuccess = EVENTS.InsertEvent(idx);
            if (bSuccess == true)
            {
                EDIT.GetEvents(); // reload side bar
                s_event_list.selectedIndex = (idx+1);
                s_event_list.focus();
                EDIT.EditEvent(false);
            }
        }
    };

    // --------------------------------
    // GetChapters
    //     Gets an updated list of chapter data
    // @param - idx : ID key of chapter to start highlighted
    // --------------------------------
    edit.GetChapters = function(idx = 0)
    {
        let strLabel = "";
        if (s_chapter_list != null)
        {
            s_chapter_list.options.length = 0;
            let chapters = EVENTS.GetChapters();
            for (let [key, item] of Object.entries(chapters))
            {
                strLabel = "[" + key + "] " + (chapters[key]["label"] != null ? chapters[key]["label"] : "CHAPTER: " + key);
                s_chapter_list.add(new Option(strLabel, key, false, false));
            }

            if (s_chapter_list.options.length > idx) { s_chapter_list.selectedIndex = idx; }
        }
    };

    // --------------------------------
    // GetEvents
    //     Gets a new events list based on the chapter selected
    // @param - idx : Index of the event we should higlhight on load
    // --------------------------------
    edit.GetEvents = function(idx = 0)
    {
        if (s_event_list != null)
        {
            let strLabel;
            s_event_list.options.length = 0;
            let events = EVENTS.GetEventsList();
            for (let [key, item] of Object.entries(events))
            {
                strLabel = "[" + key + "] " + (events[key]["label"] != null ? events[key]["label"] : "Event: " + key);
                s_event_list.add(new Option(strLabel, key, false, false));
            }

            s_event_list.selectedIndex = idx;
        }
    };

    // --------------------------------
    // NewFile
    //     Creates a new story file
    // --------------------------------
    edit.NewFile = function()
    {
        SetEditMode(true);
        EVENTS.NewEventFile();
        LoadComplete();
    };

    // --------------------------------
    // Populate
    //     Gets references to all of the HTML elements on the page
    // --------------------------------
    edit.Populate = function()
    {
        s_story_name = document.getElementById("story_name");
        s_title_screen = document.getElementById("title_screen");
        s_export_file = document.getElementById("file_name");

        s_idx_label = document.getElementById("idx");
        s_label_edit = document.getElementById("event_label");
        s_name_edit = document.getElementById("dialog_name");
        s_italics = document.getElementById("italics");
        s_bold = document.getElementById("bold");
        s_dialog_edit = document.getElementById("dialog");

        s_next_chapter = document.getElementById("chapter");
        s_next_event = document.getElementById("next");
        s_wait_time = document.getElementById("wait_time");
        s_background_scene = document.getElementById("background");
        s_background_clear = document.getElementById("clear_background");
        s_foreground_scene = document.getElementById("foreground");
        s_foreground_clear = document.getElementById("clear_foreground");
        s_effects_scene = document.getElementById("effects");
        s_effects_clear = document.getElementById("clear_effects");
        s_fade_in = document.getElementById("fadein");
        s_fade_out = document.getElementById("fadeout");

        s_choice_a = document.getElementById("divChoice_a");
        s_choice_b = document.getElementById("divChoice_b");
        s_choice_c = document.getElementById("divChoice_c");
        s_choice_d = document.getElementById("divChoice_d");
        m_arrChoices = [s_choice_a, s_choice_b, s_choice_c, s_choice_d];

        s_save_event = document.getElementById("save_event");

        s_chapter_list = document.getElementById("chapterList");
        s_event_list = document.getElementById("eventList");
    };

    // --------------------------------
    // PreviewTitle
    //    Displays the title screen file in the preview window
    // --------------------------------
    edit.PreviewTitle = function()
    {
        if (s_title_screen != null && s_title_screen.value != "")
        {
            RENDER.ClearForeground(false); // skip redraw since it will redraw when we set the background
            RENDER.ClearEffects(false);
            RENDER.SetBackground(s_title_screen.value);
        }
    };

    // --------------------------------
    // PreviewBackground
    //     Displays the background file in the preview window
    // --------------------------------
    edit.PreviewBackground = function()
    {
        if (s_background_clear != null && s_background_clear.checked)           { RENDER.ClearBackground(); }
        else if (s_background_scene != null && s_background_scene.value != "")  { RENDER.SetBackground(s_background_scene.value); }
    };

    // --------------------------------
    // PreviewForeground
    //     Displays the foreground file in the preview window
    // --------------------------------
    edit.PreviewForeground = function()
    {
        if (s_foreground_clear != null && s_foreground_clear.checked)           { RENDER.ClearForeground(); }
        else if (s_foreground_scene != null && s_foreground_scene.value != "")  { RENDER.SetForeground(s_foreground_scene.value); }
    }

    // --------------------------------
    // PreviewEffects
    //     Displays the effects file in the prview window
    // --------------------------------
    edit.PreviewEffects = function()
    {
        if (s_effects_clear != null && s_effects_clear.checked)           { RENDER.ClearEffects(); }
        else if (s_effects_scene != null && s_effects_scene.value != "")  { RENDER.SetEffects(s_effects_scene.value); }
    }

    // --------------------------------
    // RemoveEvent
    //     Removes the selected event from the current chapter
    // --------------------------------
    edit.RemoveEvent = function()
    {
        if (s_event_list != null)
        {
            let idx = s_event_list.selectedIndex;
            let bSuccess = EVENTS.RemoveEvent(idx);
            if (bSuccess == true)
            {
                EDIT.GetEvents(); // reload side bar
                EDIT.EditEvent(false);
            }
        }
    };

    // --------------------------------
    // RenameChapter
    //     Displays a popup prompt to rename the current chapter
    // --------------------------------
    edit.RenameChapter = function()
    {
        let chapter_name = prompt("Chapter Name:", "");
        if (chapter_name != "")
        {
            let current_chapter_key = EVENTS.GetCurrentChapter();
            EVENTS.SetChapterName(chapter_name);
            EDIT.GetChapters(current_chapter_key);
        }
    };

    // --------------------------------
    // RunFile
    //     Saves the current project in memory and runs the game
    // --------------------------------
    edit.RunFile = function()
    {
        let bConfirm = window.confirm("You cannot return to edit mode once the game is running - make sure you save before you continue!");
        if (bConfirm == true)
        {
            EDIT.SaveEvent();
            if (s_story_name != null && s_story_name.value != "") { EVENTS.SetStoryName(s_story_name.value); }
            if (s_title_screen != null && s_title_screen.value != "") { EVENTS.SetTitleScreen(s_title_screen.value); }

            let saved_data = EVENTS.GetEventsData();
            EVENTS.ClearEventsData();
            ExitEditMode(saved_data);
        }
    };

    // --------------------------------
    // SaveChoices
    //     Helper function for packing the choice data into the appropriate JSON-string format
    // --------------------------------
    edit.SaveChoices = function()
    {
        let idx;
        let iLength = m_arrChoices.length;
        let arrSavedChoices = [];
        for (idx = 0; idx < iLength; ++idx)
        {
            let divChoice = m_arrChoices[idx];
            if (divChoice != null)
            {
                let arrInputs = divChoice.getElementsByClassName("choice_text");
                let strText = (arrInputs != null && arrInputs.length > 0) ? arrInputs[0].value : "";
                let arrNumbers = divChoice.getElementsByClassName("short");
                let iTarget = (arrNumbers != null && arrNumbers.length > 0) ? arrNumbers[0].value : "";
                if (strText != "" && iTarget != "")
                {
                    arrSavedChoices.push( {"text":strText, "target":iTarget} );
                }
            }
        } // end for loop

        return arrSavedChoices;
    };

    // --------------------------------
    // SaveEvent
    //     Saves the current event data to the events list
    // --------------------------------
    edit.SaveEvent = function()
    {
        let objSaveData = {};

        if (s_label_edit != null && s_label_edit.value != "") { objSaveData.label = s_label_edit.value; }
        if (s_name_edit != null && s_name_edit.value != "") { objSaveData.name = s_name_edit.value; }
        if (s_italics != null && s_italics.checked) { objSaveData.italics = true; }
        if (s_bold != null && s_bold.checked) { objSaveData.bold = true; }
        if (s_dialog_edit != null && s_dialog_edit.value != "") { objSaveData.dialog = s_dialog_edit.value; }

        if (s_next_chapter != null && s_next_chapter.value != "") { objSaveData.chapter = s_next_chapter.value; }
        if (s_next_event != null && s_next_event.value != "") { objSaveData.next = s_next_event.value; }
        if (s_wait_time != null && s_wait_time.value != "") { objSaveData.wait = s_wait_time.value; }
        if (s_background_scene != null && s_background_scene.value != "") { objSaveData.background = s_background_scene.value; }
        if (s_background_clear != null && s_background_clear.checked) { objSaveData.background_clear = true; }
        if (s_foreground_scene != null && s_foreground_scene.value != "") { objSaveData.foreground = s_foreground_scene.value; }
        if (s_foreground_clear != null && s_foreground_clear.checked) { objSaveData.foreground_clear = true; }
        if (s_effects_scene != null && s_effects_scene.value != "") { objSaveData.effects = s_effects_scene.value; }
        if (s_effects_clear != null && s_effects_clear.checked) { objSaveData.effects_clear = true; }

        if (s_fade_in != null && s_fade_in.checked) { objSaveData.fadein = true; }
        if (s_fade_out != null && s_fade_out.checked) { objSaveData.fadeout = true; }

        let arrChoices = EDIT.SaveChoices();
        if (arrChoices != null && arrChoices.length > 0)
        {
            objSaveData.choice = arrChoices;
        }

        EVENTS.SaveEvent(m_iSelectedEventIdx, objSaveData);
        EDIT.GetEvents(m_iSelectedEventIdx);
    };

    // --------------------------------
    // SaveFile
    //     Saves off the story data to a donwnloadable JSON file
    // --------------------------------
    edit.SaveFile = function()
    {
        if (s_story_name != null && s_story_name.value != "") { EVENTS.SetStoryName(s_story_name.value); }
        if (s_title_screen != null && s_title_screen.value != "") { EVENTS.SetTitleScreen(s_title_screen.value); }

        let data = EVENTS.GetEventsData();
        let jsonData = JSON.stringify(data);
        let a = document.createElement("a");
        let file = new Blob([jsonData], {type:'text/plain'});
        a.href = URL.createObjectURL(file);
        a.download = (s_export_file != null && s_export_file.value != "") ? s_export_file.value : OUTPUT_FILE_DEFAULT;
        a.click();
    };

    return edit;
}()); // end of class
