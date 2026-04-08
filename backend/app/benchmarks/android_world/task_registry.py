"""
AndroidWorld Task Registry.

Defines task structures and a registry of available AndroidWorld tasks.
Tasks are parameterized to allow dynamic instantiation with random values.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Callable
import random
import string


class TaskDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class TaskCategory(str, Enum):
    DATA_ENTRY = "data_entry"
    SCREEN_READING = "screen_reading"
    MULTI_APP = "multi_app"
    COMPLEX_UI = "complex_ui_understanding"
    VERIFICATION = "verification"
    MATH_COUNTING = "math_counting"
    GAME_PLAYING = "game_playing"
    MEMORIZATION = "memorization"
    INFORMATION_RETRIEVAL = "information_retrieval"
    TRANSCRIPTION = "transcription"
    REPETITION = "repetition"
    SEARCH = "search"
    DATA_EDIT = "data_edit"
    REQUIRES_SETUP = "requires_setup"
    PARAMETERIZED = "parameterized"


@dataclass
class AndroidWorldTask:
    """Represents an AndroidWorld task with parameterization support."""

    name: str
    template: str  # Task description with {param} placeholders
    difficulty: TaskDifficulty
    categories: List[TaskCategory]
    optimal_steps: int
    target_app: Optional[str] = None  # Package name of the primary app

    # Parameter generators - called to create random values
    param_generators: Dict[str, Callable[[], Any]] = field(default_factory=dict)

    # Current instantiated parameters
    params: Dict[str, Any] = field(default_factory=dict)

    # Chef integration: marks tasks useful for testing Chef-generated apps
    chef_relevant: bool = False

    def instantiate(self, custom_params: Optional[Dict[str, Any]] = None) -> "AndroidWorldTask":
        """Create a concrete task instance with generated or custom parameters."""
        params = {}

        # Generate random values for each parameter
        for param_name, generator in self.param_generators.items():
            params[param_name] = generator()

        # Override with custom params if provided
        if custom_params:
            params.update(custom_params)

        return AndroidWorldTask(
            name=self.name,
            template=self.template,
            difficulty=self.difficulty,
            categories=self.categories,
            optimal_steps=self.optimal_steps,
            target_app=self.target_app,
            param_generators=self.param_generators,
            params=params,
            chef_relevant=self.chef_relevant,
        )

    @property
    def description(self) -> str:
        """Get the task description with parameters filled in."""
        desc = self.template
        for key, value in self.params.items():
            desc = desc.replace(f"{{{key}}}", str(value))
        return desc

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "template": self.template,
            "description": self.description,
            "difficulty": self.difficulty.value,
            "categories": [c.value for c in self.categories],
            "optimal_steps": self.optimal_steps,
            "target_app": self.target_app,
            "params": self.params,
            "chef_relevant": self.chef_relevant,
        }


# ============================================================================
# PARAMETER GENERATORS
# ============================================================================

def random_name() -> str:
    """Generate a random person name."""
    first_names = ["Alice", "Bob", "Charlie", "Diana", "Eve", "Frank", "Grace", "Henry"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller"]
    return f"{random.choice(first_names)} {random.choice(last_names)}"


def random_phone() -> str:
    """Generate a random phone number."""
    return f"+1-{random.randint(200,999)}-{random.randint(100,999)}-{random.randint(1000,9999)}"


def random_text(length: int = 50) -> str:
    """Generate random text content."""
    words = ["lorem", "ipsum", "dolor", "sit", "amet", "test", "note", "memo", "task"]
    return " ".join(random.choices(words, k=length // 5))


def random_file_name() -> str:
    """Generate a random file name."""
    return f"{''.join(random.choices(string.ascii_lowercase, k=8))}.txt"


def random_event_title() -> str:
    """Generate a random event title."""
    events = ["Meeting", "Call", "Review", "Standup", "Lunch", "Training", "Interview"]
    return f"{random.choice(events)} - {random.randint(1, 100)}"




# ============================================================================
# TASK REGISTRY - Full AndroidWorld coverage (135 tasks across 20+ apps)
# Based on arXiv:2405.14573v2 (ICLR 2025) with bonus tasks
# ============================================================================

class AndroidWorldTaskRegistry:
    """Registry of AndroidWorld tasks available for execution.

    Full coverage of the AndroidWorld benchmark (116+ tasks) across 20+ apps.
    Tasks tagged with chef_relevant=True are useful for testing Chef-generated apps.
    """

    def __init__(self):
        self._tasks: Dict[str, AndroidWorldTask] = {}
        self._register_poc_tasks()

    def _register_poc_tasks(self):
        """Register proof-of-concept tasks (subset of full AndroidWorld)."""

        # --- CONTACTS TASKS (3 tasks) ---
        _contacts = "com.android.contacts"
        self.register(AndroidWorldTask(
            name="ContactsAddContact",
            template="Create a new contact for {name}. Their number is {number}.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_contacts, chef_relevant=True,
            param_generators={"name": random_name, "number": random_phone},
        ))
        self.register(AndroidWorldTask(
            name="ContactsDeleteContact",
            template="Delete the contact named '{name}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_contacts, chef_relevant=True,
            param_generators={"name": random_name},
        ))
        self.register(AndroidWorldTask(
            name="ContactsSearchContact",
            template="Search for the contact '{name}' and read their phone number.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SEARCH, TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_contacts, chef_relevant=True,
            param_generators={"name": random_name},
        ))

        # --- CLOCK TASKS (4 tasks) ---
        _clock = "com.android.deskclock"
        self.register(AndroidWorldTask(
            name="ClockStopWatchRunning",
            template="Run the stopwatch.",
            difficulty=TaskDifficulty.EASY,
            categories=[],
            optimal_steps=3, target_app=_clock,
        ))
        self.register(AndroidWorldTask(
            name="ClockTimerEntry",
            template="Create a timer with {hours} hours, {minutes} minutes, and {seconds} seconds. Do not start the timer.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=3, target_app=_clock,
            param_generators={"hours": lambda: random.randint(0, 2), "minutes": lambda: random.randint(0, 59), "seconds": lambda: random.randint(0, 59)},
        ))
        self.register(AndroidWorldTask(
            name="ClockSetAlarm",
            template="Set an alarm for {alarm_time} with label '{alarm_label}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=6, target_app=_clock,
            param_generators={"alarm_time": random_alarm_time, "alarm_label": random_alarm_label},
        ))
        self.register(AndroidWorldTask(
            name="ClockDeleteAlarm",
            template="Delete the alarm labeled '{alarm_label}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_clock,
            param_generators={"alarm_label": random_alarm_label},
        ))

        # --- CAMERA TASKS (3 tasks) ---
        _camera = "com.android.camera2"
        self.register(AndroidWorldTask(
            name="CameraTakePhoto",
            template="Take one photo.",
            difficulty=TaskDifficulty.EASY,
            categories=[],
            optimal_steps=2, target_app=_camera,
        ))
        self.register(AndroidWorldTask(
            name="CameraSwitchToVideo",
            template="Switch to video mode and record a 5-second video.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.COMPLEX_UI],
            optimal_steps=5, target_app=_camera,
        ))
        self.register(AndroidWorldTask(
            name="CameraSwitchToFront",
            template="Switch to the front-facing camera and take a selfie.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.COMPLEX_UI],
            optimal_steps=3, target_app=_camera,
        ))

        # --- SYSTEM TASKS ---
        self.register(AndroidWorldTask(
            name="SystemBluetoothTurnOn",
            template="Turn bluetooth on.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SCREEN_READING],
            optimal_steps=2,
            target_app="com.android.settings",
        ))

        self.register(AndroidWorldTask(
            name="SystemBluetoothTurnOff",
            template="Turn bluetooth off.",
            difficulty=TaskDifficulty.EASY,
            categories=[],
            optimal_steps=2,
            target_app="com.android.settings",
        ))

        self.register(AndroidWorldTask(
            name="SystemWifiTurnOn",
            template="Turn wifi on.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SCREEN_READING],
            optimal_steps=3,
            target_app="com.android.settings",
        ))

        self.register(AndroidWorldTask(
            name="SystemWifiTurnOff",
            template="Turn wifi off.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SCREEN_READING],
            optimal_steps=3,
            target_app="com.android.settings",
        ))

        self.register(AndroidWorldTask(
            name="SystemBrightnessMax",
            template="Turn brightness to the max value.",
            difficulty=TaskDifficulty.EASY,
            categories=[],
            optimal_steps=3,
            target_app="com.android.settings",
        ))

        self.register(AndroidWorldTask(
            name="SystemCopyToClipboard",
            template="Copy the following text to the clipboard: {clipboard_content}",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=2,
            target_app=None,  # System-wide
            param_generators={"clipboard_content": lambda: random_text(20)},
        ))

        # --- Additional Settings tasks (15 total per AndroidWorld paper) ---
        _settings = "com.android.settings"
        self.register(AndroidWorldTask(
            name="SettingsAirplaneModeOn",
            template="Turn airplane mode on.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.VERIFICATION],
            optimal_steps=3, target_app=_settings,
        ))
        self.register(AndroidWorldTask(
            name="SettingsAirplaneModeOff",
            template="Turn airplane mode off.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.VERIFICATION],
            optimal_steps=3, target_app=_settings,
        ))
        self.register(AndroidWorldTask(
            name="SettingsSetScreenTimeout",
            template="Set the screen timeout to {timeout}.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.COMPLEX_UI, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_settings,
            param_generators={"timeout": random_screen_timeout},
        ))
        self.register(AndroidWorldTask(
            name="SettingsDarkModeOn",
            template="Enable dark mode (dark theme) on the device.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.COMPLEX_UI],
            optimal_steps=4, target_app=_settings,
        ))
        self.register(AndroidWorldTask(
            name="SettingsSetBrightness",
            template="Set the screen brightness to approximately {brightness_level}%.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.COMPLEX_UI, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_settings,
            param_generators={"brightness_level": random_brightness_level},
        ))
        self.register(AndroidWorldTask(
            name="SettingsLocationToggle",
            template="Turn location services on.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.VERIFICATION],
            optimal_steps=3, target_app=_settings,
        ))
        self.register(AndroidWorldTask(
            name="SettingsCheckAndroidVersion",
            template="What is the Android version on this device? Navigate to About Phone and report it.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.SCREEN_READING],
            optimal_steps=4, target_app=_settings,
        ))
        self.register(AndroidWorldTask(
            name="SettingsAutoRotateOn",
            template="Enable auto-rotate on the device.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.VERIFICATION],
            optimal_steps=3, target_app=_settings,
        ))
        self.register(AndroidWorldTask(
            name="SettingsSetRingtoneVolume",
            template="Set the ringtone volume to approximately {volume_level}%.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.COMPLEX_UI, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_settings,
            param_generators={"volume_level": random_ringtone_volume},
        ))
        self.register(AndroidWorldTask(
            name="SettingsNotificationCheck",
            template="Check which apps have notifications enabled in the notification settings.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.SCREEN_READING],
            optimal_steps=5, target_app=_settings,
        ))

        # --- OPEN APP TASK ---
        self.register(AndroidWorldTask(
            name="OpenAppTaskEval",
            template="Open the {app_name} app. Clear any pop-ups that may appear by granting all permissions that are required.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.PARAMETERIZED],
            optimal_steps=2,
            param_generators={"app_name": lambda: random.choice(["Settings", "Contacts", "Clock", "Calculator"])},
        ))

        # Register expanded tasks
        self._register_markor_tasks()
        self._register_calendar_tasks()
        self._register_expense_tasks()
        self._register_recipe_tasks()
        self._register_messaging_tasks()
        self._register_browser_tasks()
        self._register_files_tasks()
        self._register_multi_app_tasks()

        # Phase 0 expansion
        self._register_calculator_tasks()
        self._register_gallery_tasks()
        self._register_music_tasks()
        self._register_maps_tasks()

        # Full coverage expansion: 6 new apps (22 tasks)
        self._register_opentracks_tasks()
        self._register_tasks_app_tasks()
        self._register_joplin_tasks()
        self._register_vlc_tasks()
        self._register_audio_recorder_tasks()
        self._register_simple_draw_tasks()

    def _register_markor_tasks(self):
        """Register Markor note-taking tasks (14 tasks per AndroidWorld paper)."""
        _mk = "net.gsantner.markor"

        self.register(AndroidWorldTask(
            name="MarkorCreateNote",
            template="Create a new note in Markor named {file_name} with the following text: {note_content}",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=6, target_app=_mk, chef_relevant=True,
            param_generators={"file_name": random_markor_file, "note_content": random_note_content},
        ))
        self.register(AndroidWorldTask(
            name="MarkorDeleteNote",
            template="Delete the note named '{file_name}' in Markor.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_mk, chef_relevant=True,
            param_generators={"file_name": random_markor_file},
        ))
        self.register(AndroidWorldTask(
            name="MarkorEditNote",
            template="Edit {file_name} in Markor. Add the following text at the end: {note_content}",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_mk, chef_relevant=True,
            param_generators={"file_name": random_markor_file, "note_content": random_note_content},
        ))
        self.register(AndroidWorldTask(
            name="MarkorEditNoteHeader",
            template="Update the Markor note '{file_name}' by adding the following text, along with a new blank line before the existing content: '{header_text}'",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=7, target_app=_mk,
            param_generators={"file_name": random_markor_file, "header_text": random_header_text},
        ))
        self.register(AndroidWorldTask(
            name="MarkorEditNoteRename",
            template="Update the Markor note '{file_name}' by adding text '{header_text}' and rename it to '{new_file_name}'.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=9, target_app=_mk,
            param_generators={"file_name": random_markor_file, "header_text": random_header_text, "new_file_name": random_markor_file},
        ))
        self.register(AndroidWorldTask(
            name="MarkorSearchNote",
            template="Search for notes containing '{search_term}' in Markor.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SEARCH, TaskCategory.PARAMETERIZED],
            optimal_steps=3, target_app=_mk,
            param_generators={"search_term": lambda: random.choice(["meeting", "project", "task", "notes"])},
        ))
        self.register(AndroidWorldTask(
            name="MarkorCreateFolder",
            template="Create a new folder named '{folder_name}' in Markor.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_mk, chef_relevant=True,
            param_generators={"folder_name": random_folder_name},
        ))
        self.register(AndroidWorldTask(
            name="MarkorMoveNote",
            template="Move the note '{file_name}' to the folder '{folder_name}' in Markor.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=6, target_app=_mk,
            param_generators={"file_name": random_markor_file, "folder_name": random_folder_name},
        ))
        self.register(AndroidWorldTask(
            name="MarkorDeleteAllNotes",
            template="Delete all notes in the current directory in Markor.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.REPETITION],
            optimal_steps=10, target_app=_mk,
        ))
        self.register(AndroidWorldTask(
            name="MarkorShareViaSms",
            template="Create a new note in Markor named {file_name} with the text: {note_content}. Share the entire content of the note with the phone number {number} via SMS.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.MULTI_APP, TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=10, target_app=_mk,
            param_generators={"file_name": random_markor_file, "note_content": random_note_content, "number": random_phone},
        ))
        self.register(AndroidWorldTask(
            name="MarkorCreateTodoList",
            template="Create a new to-do list in Markor named '{file_name}' with items: {note_content}",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=6, target_app=_mk, chef_relevant=True,
            param_generators={"file_name": random_markor_file, "note_content": lambda: ", ".join(random.sample(["Buy groceries", "Call dentist", "Review PR", "Update docs", "Send invoice"], k=3))},
        ))
        self.register(AndroidWorldTask(
            name="MarkorCountNotes",
            template="How many notes are in the current directory in Markor? Express your answer as a single integer.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.MATH_COUNTING],
            optimal_steps=3, target_app=_mk,
        ))
        self.register(AndroidWorldTask(
            name="MarkorViewRecent",
            template="Open the most recently modified note in Markor and read its content.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SCREEN_READING],
            optimal_steps=3, target_app=_mk,
        ))
        self.register(AndroidWorldTask(
            name="MarkorFormatNote",
            template="Create a note '{file_name}' in Markor and format the heading as bold markdown (## heading).",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.COMPLEX_UI, TaskCategory.PARAMETERIZED],
            optimal_steps=7, target_app=_mk,
            param_generators={"file_name": random_markor_file},
        ))

    def _register_calendar_tasks(self):
        """Register Simple Calendar Pro tasks (17 tasks per AndroidWorld paper)."""
        _cal = "com.simplemobiletools.calendar.pro"

        self.register(AndroidWorldTask(
            name="CalendarCreateEvent",
            template="In Simple Calendar Pro, create a calendar event on day {day_offset} from today at {hour}h with the title '{event_title}' and the description '{event_description}'. The event should last for {event_duration} mins.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=8, target_app=_cal, chef_relevant=True,
            param_generators={"event_title": random_event_title, "day_offset": random_date_offset, "hour": random_time_hour, "event_description": random_event_description, "event_duration": random_event_duration},
        ))
        self.register(AndroidWorldTask(
            name="CalendarCreateRecurringEvent",
            template="In Simple Calendar Pro, create a recurring calendar event titled '{event_title}' starting on day {day_offset} at {hour}h. The event recurs {repeat_interval}, forever, and lasts for {event_duration} minutes each occurrence.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.COMPLEX_UI, TaskCategory.PARAMETERIZED],
            optimal_steps=12, target_app=_cal, chef_relevant=True,
            param_generators={"event_title": random_event_title, "day_offset": random_date_offset, "hour": random_time_hour, "repeat_interval": random_repeat_interval, "event_duration": random_event_duration},
        ))
        self.register(AndroidWorldTask(
            name="CalendarDeleteEvent",
            template="In Simple Calendar Pro, delete the calendar event titled '{event_title}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_cal, chef_relevant=True,
            param_generators={"event_title": random_event_title},
        ))
        self.register(AndroidWorldTask(
            name="CalendarDeleteEventsOnDay",
            template="In Simple Calendar Pro, delete all events scheduled for this {day_of_week}.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.REPETITION, TaskCategory.PARAMETERIZED],
            optimal_steps=10, target_app=_cal,
            param_generators={"day_of_week": random_day_of_week},
        ))
        self.register(AndroidWorldTask(
            name="CalendarEditEvent",
            template="In Simple Calendar Pro, edit the event '{event_title}' and change its description to '{event_description}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=7, target_app=_cal, chef_relevant=True,
            param_generators={"event_title": random_event_title, "event_description": random_event_description},
        ))
        self.register(AndroidWorldTask(
            name="CalendarEventsOnDate",
            template="What events do I have on day {day_offset} from today in Simple Calendar Pro? Answer with the titles only.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.SCREEN_READING, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_cal,
            param_generators={"day_offset": random_date_offset},
        ))
        self.register(AndroidWorldTask(
            name="CalendarEventsOnRelativeDay",
            template="What events do I have this {day_of_week} in Simple Calendar Pro? Answer with the titles only.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.SCREEN_READING, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_cal,
            param_generators={"day_of_week": random_day_of_week},
        ))
        self.register(AndroidWorldTask(
            name="CalendarCreateAllDayEvent",
            template="In Simple Calendar Pro, create an all-day event titled '{event_title}' on day {day_offset} from today.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=7, target_app=_cal, chef_relevant=True,
            param_generators={"event_title": random_event_title, "day_offset": random_date_offset},
        ))
        self.register(AndroidWorldTask(
            name="CalendarSetReminder",
            template="In Simple Calendar Pro, create an event '{event_title}' with a {reminder_minutes} minute reminder.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=10, target_app=_cal,
            param_generators={"event_title": random_event_title, "reminder_minutes": lambda: random.choice([10, 15, 30, 60])},
        ))
        self.register(AndroidWorldTask(
            name="CalendarViewToday",
            template="View today's calendar events in Simple Calendar Pro.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SCREEN_READING],
            optimal_steps=3, target_app=_cal,
        ))
        self.register(AndroidWorldTask(
            name="CalendarViewWeek",
            template="Switch to the weekly view in Simple Calendar Pro.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.COMPLEX_UI],
            optimal_steps=3, target_app=_cal,
        ))
        self.register(AndroidWorldTask(
            name="CalendarCountEventsOnDay",
            template="How many events do I have this {day_of_week} in Simple Calendar Pro? Express your answer as a single integer.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.MATH_COUNTING, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_cal,
            param_generators={"day_of_week": random_day_of_week},
        ))
        self.register(AndroidWorldTask(
            name="CalendarCreateEventWithLocation",
            template="In Simple Calendar Pro, create an event titled '{event_title}' at {hour}h on day {day_offset} at location '{location}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=9, target_app=_cal, chef_relevant=True,
            param_generators={"event_title": random_event_title, "hour": random_time_hour, "day_offset": random_date_offset, "location": random_location},
        ))
        self.register(AndroidWorldTask(
            name="CalendarDeleteAllEvents",
            template="In Simple Calendar Pro, delete all events scheduled for the current week.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.REPETITION],
            optimal_steps=15, target_app=_cal,
        ))
        self.register(AndroidWorldTask(
            name="CalendarEventDuration",
            template="How long is the event '{event_title}' in Simple Calendar Pro? Answer in minutes.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.SCREEN_READING, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_cal,
            param_generators={"event_title": random_event_title},
        ))
        self.register(AndroidWorldTask(
            name="CalendarMoveEvent",
            template="In Simple Calendar Pro, move the event '{event_title}' to day {day_offset} from today.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=8, target_app=_cal,
            param_generators={"event_title": random_event_title, "day_offset": random_date_offset},
        ))
        self.register(AndroidWorldTask(
            name="CalendarChangeEventColor",
            template="In Simple Calendar Pro, change the color of event '{event_title}' to a different color.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.COMPLEX_UI, TaskCategory.PARAMETERIZED],
            optimal_steps=7, target_app=_cal,
            param_generators={"event_title": random_event_title},
        ))

    def _register_expense_tasks(self):
        """Register Pro Expense tracking tasks (9 tasks per AndroidWorld paper)."""
        _exp = "com.arduia.expense"

        self.register(AndroidWorldTask(
            name="ExpenseAddEntry",
            template="In Pro Expense, add an expense of {amount} for '{category}' with note: '{description}'",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=7, target_app=_exp, chef_relevant=True,
            param_generators={"amount": random_amount, "category": random_expense_category, "description": lambda: random.choice(["lunch", "coffee", "taxi", "supplies", "meeting"])},
        ))
        self.register(AndroidWorldTask(
            name="ExpenseDeleteEntry",
            template="In Pro Expense, delete the most recent expense entry.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.DATA_EDIT],
            optimal_steps=4, target_app=_exp, chef_relevant=True,
        ))
        self.register(AndroidWorldTask(
            name="ExpenseViewSummary",
            template="In Pro Expense, view the total expenses for this month.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SCREEN_READING],
            optimal_steps=3, target_app=_exp,
        ))
        self.register(AndroidWorldTask(
            name="ExpenseFilterByCategory",
            template="In Pro Expense, filter expenses to show only '{category}' category.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SEARCH, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_exp,
            param_generators={"category": random_expense_category},
        ))
        self.register(AndroidWorldTask(
            name="ExpenseEditEntry",
            template="In Pro Expense, edit the most recent expense and change the amount to {amount}.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=6, target_app=_exp, chef_relevant=True,
            param_generators={"amount": random_amount},
        ))
        self.register(AndroidWorldTask(
            name="ExpenseExportCsv",
            template="In Pro Expense, export all expenses as a CSV file.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.COMPLEX_UI],
            optimal_steps=5, target_app=_exp,
        ))
        self.register(AndroidWorldTask(
            name="ExpenseAddRecurring",
            template="In Pro Expense, add a recurring expense of {amount} for '{category}' that repeats {repeat_interval}.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=9, target_app=_exp,
            param_generators={"amount": random_amount, "category": random_expense_category, "repeat_interval": random_repeat_interval},
        ))
        self.register(AndroidWorldTask(
            name="ExpenseCountByCategory",
            template="In Pro Expense, how many expenses are in the '{category}' category? Express as a single integer.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.MATH_COUNTING, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_exp,
            param_generators={"category": random_expense_category},
        ))
        self.register(AndroidWorldTask(
            name="ExpenseDeleteAll",
            template="In Pro Expense, delete all expense entries.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.REPETITION],
            optimal_steps=10, target_app=_exp,
        ))

    def _register_recipe_tasks(self):
        """Register Broccoli recipe management tasks (13 tasks per AndroidWorld paper)."""
        _rcp = "com.flauschcode.broccoli"

        self.register(AndroidWorldTask(
            name="RecipeAddNew",
            template="In Broccoli, add a new recipe called '{recipe_name}' with ingredients: {ingredients}",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=8, target_app=_rcp, chef_relevant=True,
            param_generators={"recipe_name": random_recipe_name, "ingredients": random_ingredient},
        ))
        self.register(AndroidWorldTask(
            name="RecipeAddWithInstructions",
            template="In Broccoli, add a recipe '{recipe_name}' with ingredients: {ingredients}. Instructions: {instructions}",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=12, target_app=_rcp, chef_relevant=True,
            param_generators={"recipe_name": random_recipe_name, "ingredients": random_ingredient, "instructions": random_recipe_instructions},
        ))
        self.register(AndroidWorldTask(
            name="RecipeDelete",
            template="In Broccoli, delete the recipe called '{recipe_name}'.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_rcp, chef_relevant=True,
            param_generators={"recipe_name": random_recipe_name},
        ))
        self.register(AndroidWorldTask(
            name="RecipeSearch",
            template="In Broccoli, search for recipes containing '{search_term}'.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SEARCH, TaskCategory.PARAMETERIZED],
            optimal_steps=3, target_app=_rcp, chef_relevant=True,
            param_generators={"search_term": lambda: random.choice(["pasta", "chicken", "veggie", "quick"])},
        ))
        self.register(AndroidWorldTask(
            name="RecipeEditIngredients",
            template="In Broccoli, edit the recipe '{recipe_name}' and add '{ingredients}' to the ingredients list.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=7, target_app=_rcp, chef_relevant=True,
            param_generators={"recipe_name": random_recipe_name, "ingredients": random_ingredient},
        ))
        self.register(AndroidWorldTask(
            name="RecipeFilterByCategory",
            template="In Broccoli, filter recipes to show only '{category}' recipes.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SEARCH, TaskCategory.PARAMETERIZED],
            optimal_steps=3, target_app=_rcp,
            param_generators={"category": random_recipe_category},
        ))
        self.register(AndroidWorldTask(
            name="RecipeAddToFavorites",
            template="In Broccoli, mark the recipe '{recipe_name}' as a favorite.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_rcp,
            param_generators={"recipe_name": random_recipe_name},
        ))
        self.register(AndroidWorldTask(
            name="RecipeCountAll",
            template="How many recipes are saved in Broccoli? Express your answer as a single integer.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.MATH_COUNTING],
            optimal_steps=3, target_app=_rcp,
        ))
        self.register(AndroidWorldTask(
            name="RecipeShareViaText",
            template="In Broccoli, share the recipe '{recipe_name}' via text message to {phone_number}.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.MULTI_APP, TaskCategory.PARAMETERIZED],
            optimal_steps=8, target_app=_rcp,
            param_generators={"recipe_name": random_recipe_name, "phone_number": random_phone},
        ))
        self.register(AndroidWorldTask(
            name="RecipeAddCategory",
            template="In Broccoli, create a new recipe category called '{category}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_rcp, chef_relevant=True,
            param_generators={"category": random_recipe_category},
        ))
        self.register(AndroidWorldTask(
            name="RecipeViewDetails",
            template="In Broccoli, open the recipe '{recipe_name}' and read the full instructions.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SCREEN_READING, TaskCategory.PARAMETERIZED],
            optimal_steps=3, target_app=_rcp, chef_relevant=True,
            param_generators={"recipe_name": random_recipe_name},
        ))
        self.register(AndroidWorldTask(
            name="RecipeDuplicateAndEdit",
            template="In Broccoli, duplicate the recipe '{recipe_name}' and rename the copy to '{new_name}'.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=10, target_app=_rcp,
            param_generators={"recipe_name": random_recipe_name, "new_name": random_recipe_name},
        ))
        self.register(AndroidWorldTask(
            name="RecipeDeleteAll",
            template="In Broccoli, delete all recipes in the current category.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.REPETITION],
            optimal_steps=12, target_app=_rcp,
        ))

    def _register_messaging_tasks(self):
        """Register Simple SMS Messenger tasks (7 tasks per AndroidWorld paper)."""
        _sms = "com.simplemobiletools.smsmessenger"

        self.register(AndroidWorldTask(
            name="SmsComposeMessage",
            template="In Simple SMS Messenger, compose a new SMS to {phone_number} with message: '{message}'",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_sms, chef_relevant=True,
            param_generators={"phone_number": random_phone, "message": lambda: random.choice(["Hello, this is a test message.", "Can you call me back?", "Meeting at 3pm confirmed."])},
        ))
        self.register(AndroidWorldTask(
            name="SmsReadLastMessage",
            template="In Simple SMS Messenger, open the most recent conversation and read the last message.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SCREEN_READING],
            optimal_steps=3, target_app=_sms,
        ))
        self.register(AndroidWorldTask(
            name="SmsDeleteConversation",
            template="In Simple SMS Messenger, delete the conversation with {phone_number}.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_sms, chef_relevant=True,
            param_generators={"phone_number": random_phone},
        ))
        self.register(AndroidWorldTask(
            name="SmsSearchMessages",
            template="In Simple SMS Messenger, search for messages containing '{search_term}'.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SEARCH, TaskCategory.PARAMETERIZED],
            optimal_steps=3, target_app=_sms,
            param_generators={"search_term": lambda: random.choice(["meeting", "hello", "call", "tomorrow"])},
        ))
        self.register(AndroidWorldTask(
            name="SmsForwardMessage",
            template="In Simple SMS Messenger, forward the last message from {phone_number} to {forward_number}.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.MULTI_APP, TaskCategory.PARAMETERIZED],
            optimal_steps=8, target_app=_sms,
            param_generators={"phone_number": random_phone, "forward_number": random_phone},
        ))
        self.register(AndroidWorldTask(
            name="SmsCountUnread",
            template="In Simple SMS Messenger, how many unread conversations are there? Express as a single integer.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.MATH_COUNTING],
            optimal_steps=3, target_app=_sms,
        ))
        self.register(AndroidWorldTask(
            name="SmsComposeToContact",
            template="In Simple SMS Messenger, send an SMS to the contact '{name}' saying '{message}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.MULTI_APP, TaskCategory.PARAMETERIZED],
            optimal_steps=6, target_app=_sms, chef_relevant=True,
            param_generators={"name": random_name, "message": lambda: random.choice(["See you at 5pm", "Thanks for the update", "Please confirm"])},
        ))

    def _register_browser_tasks(self):
        """Register browser tasks."""
        self.register(AndroidWorldTask(
            name="BrowserNavigateToUrl",
            template="Open Chrome and navigate to {url}",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=3,
            target_app="com.android.chrome",
            param_generators={"url": lambda: random.choice([
                "google.com", "github.com", "wikipedia.org", "android.com"
            ])},
        ))

        self.register(AndroidWorldTask(
            name="BrowserSearchGoogle",
            template="Search Google for '{search_query}'",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SEARCH, TaskCategory.PARAMETERIZED],
            optimal_steps=4,
            target_app="com.android.chrome",
            param_generators={"search_query": lambda: random.choice([
                "weather today", "android development", "python tutorial", "latest news"
            ])},
        ))

        self.register(AndroidWorldTask(
            name="BrowserOpenNewTab",
            template="Open a new tab in Chrome.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.COMPLEX_UI],
            optimal_steps=2,
            target_app="com.android.chrome",
        ))

    def _register_files_tasks(self):
        """Register file manager tasks."""
        self.register(AndroidWorldTask(
            name="FilesCreateFolder",
            template="Create a new folder named '{folder_name}' in Downloads.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=5,
            target_app="com.android.documentsui",
            param_generators={"folder_name": lambda: f"TestFolder_{random.randint(100, 999)}"},
        ))

        self.register(AndroidWorldTask(
            name="FilesDeleteFile",
            template="Delete the file named '{file_name}' from Downloads.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=4,
            target_app="com.android.documentsui",
            param_generators={"file_name": random_file_name},
        ))

        self.register(AndroidWorldTask(
            name="FilesSearchFile",
            template="Search for files containing '{search_term}'.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SEARCH, TaskCategory.PARAMETERIZED],
            optimal_steps=3,
            target_app="com.android.documentsui",
            param_generators={"search_term": lambda: random.choice(["test", "document", "image", "download"])},
        ))

    def _register_multi_app_tasks(self):
        """Register multi-app workflow tasks (harder, complex tasks)."""
        self.register(AndroidWorldTask(
            name="MultiAppContactToSms",
            template="Find the contact '{name}' and send them an SMS saying '{message}'",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.MULTI_APP, TaskCategory.PARAMETERIZED],
            optimal_steps=8,
            param_generators={
                "name": random_name,
                "message": lambda: "Hello! How are you?",
            },
        ))

        self.register(AndroidWorldTask(
            name="MultiAppCalendarToReminder",
            template="Check today's calendar events and create a reminder for the first event.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.MULTI_APP, TaskCategory.SCREEN_READING],
            optimal_steps=10,
        ))

        self.register(AndroidWorldTask(
            name="MultiAppBrowserToNotes",
            template="Search for '{search_query}' in Chrome and save the first result title to a note in Markor.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.MULTI_APP, TaskCategory.PARAMETERIZED, TaskCategory.TRANSCRIPTION],
            optimal_steps=12,
            param_generators={"search_query": lambda: random.choice([
                "Python tutorial", "Android tips", "productivity apps"
            ])},
        ))

        self.register(AndroidWorldTask(
            name="MultiAppPhotosToShare",
            template="Take a photo and share it via the Messages app to {phone_number}.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.MULTI_APP, TaskCategory.PARAMETERIZED],
            optimal_steps=10,
            param_generators={"phone_number": random_phone},
        ))

        self.register(AndroidWorldTask(
            name="MultiAppExpenseFromReceipt",
            template="Open the camera, take a photo of a receipt, then add an expense of {amount} for '{category}'.",
            difficulty=TaskDifficulty.HARD,
            categories=[TaskCategory.MULTI_APP, TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=12,
            param_generators={
                "amount": random_amount,
                "category": random_expense_category,
            },
        ))


    def _register_calculator_tasks(self):
        """Register Calculator app tasks (4 tasks)."""
        self.register(AndroidWorldTask(
            name="CalculatorBasicArithmetic",
            template="Open the Calculator and calculate {num1} {operator} {num2}.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.MATH_COUNTING, TaskCategory.PARAMETERIZED],
            optimal_steps=4,
            target_app="com.android.calculator2",
            param_generators={
                "num1": random_number,
                "num2": random_number,
                "operator": random_operator,
            },
        ))

        self.register(AndroidWorldTask(
            name="CalculatorScientificMode",
            template="Switch to scientific mode and calculate sin({angle}).",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.COMPLEX_UI, TaskCategory.MATH_COUNTING, TaskCategory.PARAMETERIZED],
            optimal_steps=6,
            target_app="com.android.calculator2",
            param_generators={"angle": random_angle},
        ))

        self.register(AndroidWorldTask(
            name="CalculatorViewHistory",
            template="Open the Calculator and view the calculation history.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SCREEN_READING],
            optimal_steps=3,
            target_app="com.android.calculator2",
        ))

        self.register(AndroidWorldTask(
            name="CalculatorMemoryStore",
            template="Calculate {num1} + {num2} and store the result in memory.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.MATH_COUNTING, TaskCategory.PARAMETERIZED],
            optimal_steps=6,
            target_app="com.android.calculator2",
            param_generators={"num1": random_number, "num2": random_number},
        ))

    def _register_gallery_tasks(self):
        """Register Simple Gallery Pro tasks (4 tasks per AndroidWorld paper)."""
        _gal = "com.simplemobiletools.gallery.pro"

        self.register(AndroidWorldTask(
            name="GalleryViewPhoto",
            template="Open Simple Gallery Pro and view the most recent photo.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SCREEN_READING],
            optimal_steps=3, target_app=_gal,
        ))
        self.register(AndroidWorldTask(
            name="GalleryDeletePhoto",
            template="Open Simple Gallery Pro and delete the most recent photo.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT],
            optimal_steps=5, target_app=_gal,
        ))
        self.register(AndroidWorldTask(
            name="GallerySharePhoto",
            template="Open Simple Gallery Pro and share the most recent photo via {share_app}.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.MULTI_APP, TaskCategory.PARAMETERIZED],
            optimal_steps=6, target_app=_gal,
            param_generators={"share_app": random_share_app},
        ))
        self.register(AndroidWorldTask(
            name="GalleryCountPhotos",
            template="How many photos are in Simple Gallery Pro? Express your answer as a single integer.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.MATH_COUNTING],
            optimal_steps=3, target_app=_gal,
        ))

    def _register_music_tasks(self):
        """Register Retro Music player tasks (4 tasks per AndroidWorld paper)."""
        _music = "code.name.monkey.retromusic"

        self.register(AndroidWorldTask(
            name="MusicPlaySong",
            template="Open Retro Music and play any available song.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.COMPLEX_UI],
            optimal_steps=4, target_app=_music,
        ))
        self.register(AndroidWorldTask(
            name="MusicCreatePlaylist",
            template="Create a new playlist named '{playlist_name}' in Retro Music.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=6, target_app=_music, chef_relevant=True,
            param_generators={"playlist_name": random_playlist_name},
        ))
        self.register(AndroidWorldTask(
            name="MusicSearchSong",
            template="In Retro Music, search for a song containing '{search_term}'.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SEARCH, TaskCategory.PARAMETERIZED],
            optimal_steps=3, target_app=_music,
            param_generators={"search_term": lambda: random.choice(["love", "night", "dream", "rock", "jazz"])},
        ))
        self.register(AndroidWorldTask(
            name="MusicDeletePlaylist",
            template="In Retro Music, delete the playlist named '{playlist_name}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_music, chef_relevant=True,
            param_generators={"playlist_name": random_playlist_name},
        ))

    def _register_maps_tasks(self):
        """Register OsmAnd Maps tasks (3 tasks per AndroidWorld paper)."""
        _maps = "net.osmand.plus"

        self.register(AndroidWorldTask(
            name="MapsSearchLocation",
            template="In OsmAnd, search for '{location}'.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SEARCH, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_maps,
            param_generators={"location": random_location},
        ))
        self.register(AndroidWorldTask(
            name="MapsGetDirections",
            template="In OsmAnd, get directions from current location to '{destination}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.SEARCH, TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.PARAMETERIZED],
            optimal_steps=6, target_app=_maps,
            param_generators={"destination": random_destination},
        ))
        self.register(AndroidWorldTask(
            name="MapsAddBookmark",
            template="In OsmAnd, bookmark the location '{location}' for later.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_maps,
            param_generators={"location": random_location},
        ))

    def _register_opentracks_tasks(self):
        """Register OpenTracks sports tracking tasks (6 tasks per AndroidWorld paper)."""
        _ot = "de.dennisguse.opentracks"

        self.register(AndroidWorldTask(
            name="OpenTracksStartActivity",
            template="In OpenTracks, start a new {activity_type} activity.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_ot, chef_relevant=True,
            param_generators={"activity_type": random_activity_type},
        ))
        self.register(AndroidWorldTask(
            name="OpenTracksStopActivity",
            template="In OpenTracks, stop the currently running activity.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.COMPLEX_UI],
            optimal_steps=3, target_app=_ot,
        ))
        self.register(AndroidWorldTask(
            name="OpenTracksViewStats",
            template="In OpenTracks, view the statistics for the most recent activity.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SCREEN_READING, TaskCategory.INFORMATION_RETRIEVAL],
            optimal_steps=4, target_app=_ot,
        ))
        self.register(AndroidWorldTask(
            name="OpenTracksDeleteActivity",
            template="In OpenTracks, delete the most recent activity.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT],
            optimal_steps=5, target_app=_ot, chef_relevant=True,
        ))
        self.register(AndroidWorldTask(
            name="OpenTracksCountActivities",
            template="In OpenTracks, how many activities have been recorded? Express as a single integer.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.MATH_COUNTING],
            optimal_steps=3, target_app=_ot,
        ))
        self.register(AndroidWorldTask(
            name="OpenTracksExportActivity",
            template="In OpenTracks, export the most recent activity as a GPX file.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.COMPLEX_UI],
            optimal_steps=6, target_app=_ot,
        ))

    def _register_tasks_app_tasks(self):
        """Register Tasks.org task management tasks (6 tasks per AndroidWorld paper)."""
        _tasks = "org.tasks"

        self.register(AndroidWorldTask(
            name="TasksCreateTask",
            template="In Tasks, create a new task titled '{task_title}' with priority '{task_priority}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=6, target_app=_tasks, chef_relevant=True,
            param_generators={"task_title": random_task_title, "task_priority": random_task_priority},
        ))
        self.register(AndroidWorldTask(
            name="TasksCompleteTask",
            template="In Tasks, mark the task '{task_title}' as complete.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_tasks, chef_relevant=True,
            param_generators={"task_title": random_task_title},
        ))
        self.register(AndroidWorldTask(
            name="TasksDeleteTask",
            template="In Tasks, delete the task titled '{task_title}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_tasks, chef_relevant=True,
            param_generators={"task_title": random_task_title},
        ))
        self.register(AndroidWorldTask(
            name="TasksSetDueDate",
            template="In Tasks, set the due date of '{task_title}' to {due_date}.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=7, target_app=_tasks,
            param_generators={"task_title": random_task_title, "due_date": random_due_date_str},
        ))
        self.register(AndroidWorldTask(
            name="TasksFilterByPriority",
            template="In Tasks, filter to show only '{task_priority}' priority tasks.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SEARCH, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_tasks,
            param_generators={"task_priority": random_task_priority},
        ))
        self.register(AndroidWorldTask(
            name="TasksCountPending",
            template="In Tasks, how many incomplete tasks are there? Express as a single integer.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.INFORMATION_RETRIEVAL, TaskCategory.MATH_COUNTING],
            optimal_steps=3, target_app=_tasks,
        ))

    def _register_joplin_tasks(self):
        """Register Joplin note-taking tasks (4 tasks per AndroidWorld paper)."""
        _jop = "net.cozic.joplin"

        self.register(AndroidWorldTask(
            name="JoplinCreateNote",
            template="In Joplin, create a new note titled '{note_title}' in notebook '{notebook_name}' with content: {joplin_content}",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=7, target_app=_jop, chef_relevant=True,
            param_generators={"note_title": random_note_title, "notebook_name": random_notebook_name, "joplin_content": random_joplin_content},
        ))
        self.register(AndroidWorldTask(
            name="JoplinCreateNotebook",
            template="In Joplin, create a new notebook named '{notebook_name}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_jop, chef_relevant=True,
            param_generators={"notebook_name": random_notebook_name},
        ))
        self.register(AndroidWorldTask(
            name="JoplinSearchNotes",
            template="In Joplin, search for notes containing '{search_term}'.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SEARCH, TaskCategory.PARAMETERIZED],
            optimal_steps=3, target_app=_jop,
            param_generators={"search_term": lambda: random.choice(["meeting", "project", "research", "draft"])},
        ))
        self.register(AndroidWorldTask(
            name="JoplinDeleteNote",
            template="In Joplin, delete the note titled '{note_title}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_EDIT, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_jop, chef_relevant=True,
            param_generators={"note_title": random_note_title},
        ))

    def _register_vlc_tasks(self):
        """Register VLC media player tasks (3 tasks per AndroidWorld paper)."""
        _vlc = "org.videolan.vlc"

        self.register(AndroidWorldTask(
            name="VlcPlayMedia",
            template="In VLC, play the media file '{media_file}'.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.COMPLEX_UI, TaskCategory.PARAMETERIZED],
            optimal_steps=4, target_app=_vlc,
            param_generators={"media_file": random_media_file},
        ))
        self.register(AndroidWorldTask(
            name="VlcCreatePlaylist",
            template="In VLC, create a new playlist with the following files: {vlc_files}",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=8, target_app=_vlc, chef_relevant=True,
            param_generators={"vlc_files": random_vlc_files},
        ))
        self.register(AndroidWorldTask(
            name="VlcBrowseFiles",
            template="In VLC, browse the local storage and list all available media files.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.SCREEN_READING, TaskCategory.INFORMATION_RETRIEVAL],
            optimal_steps=4, target_app=_vlc,
        ))

    def _register_audio_recorder_tasks(self):
        """Register Audio Recorder tasks (2 tasks per AndroidWorld paper)."""
        _ar = "com.dimowner.audiorecorder"

        self.register(AndroidWorldTask(
            name="AudioRecorderRecord",
            template="In Audio Recorder, start a new recording named '{recording_name}'.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.PARAMETERIZED],
            optimal_steps=5, target_app=_ar,
            param_generators={"recording_name": random_recording_name},
        ))
        self.register(AndroidWorldTask(
            name="AudioRecorderPlayback",
            template="In Audio Recorder, play back the most recent recording.",
            difficulty=TaskDifficulty.EASY,
            categories=[TaskCategory.COMPLEX_UI],
            optimal_steps=3, target_app=_ar,
        ))

    def _register_simple_draw_tasks(self):
        """Register Simple Draw Pro tasks (1 task per AndroidWorld paper)."""
        _draw = "com.simplemobiletools.draw.pro"

        self.register(AndroidWorldTask(
            name="SimpleDrawCreate",
            template="In Simple Draw Pro, create a new drawing named '{drawing_name}' and save it.",
            difficulty=TaskDifficulty.MEDIUM,
            categories=[TaskCategory.DATA_ENTRY, TaskCategory.COMPLEX_UI, TaskCategory.PARAMETERIZED],
            optimal_steps=6, target_app=_draw,
            param_generators={"drawing_name": random_drawing_name},
        ))

    def register(self, task: AndroidWorldTask):
        """Register a task in the registry."""
        self._tasks[task.name] = task

    def get(self, name: str) -> Optional[AndroidWorldTask]:
        """Get a task by name."""
        return self._tasks.get(name)

    def get_instantiated(self, name: str, params: Optional[Dict[str, Any]] = None) -> Optional[AndroidWorldTask]:
        """Get an instantiated task with generated or custom parameters."""
        task = self._tasks.get(name)
        if task:
            return task.instantiate(params)
        return None

    def list_tasks(self,
                   difficulty: Optional[TaskDifficulty] = None,
                   category: Optional[TaskCategory] = None) -> List[AndroidWorldTask]:
        """List tasks, optionally filtered by difficulty or category."""
        tasks = list(self._tasks.values())

        if difficulty:
            tasks = [t for t in tasks if t.difficulty == difficulty]

        if category:
            tasks = [t for t in tasks if category in t.categories]

        return tasks

    def list_task_names(self) -> List[str]:
        """List all registered task names."""
        return list(self._tasks.keys())

    def list_chef_relevant_tasks(self) -> List[AndroidWorldTask]:
        """List tasks relevant for Chef integration testing (CRUD, forms, state)."""
        return [t for t in self._tasks.values() if t.chef_relevant]

    def list_tasks_by_app(self, target_app: str) -> List[AndroidWorldTask]:
        """List all tasks for a specific target app."""
        return [t for t in self._tasks.values() if t.target_app == target_app]

    def get_app_names(self) -> List[str]:
        """Get unique list of target app package names."""
        return sorted(set(t.target_app for t in self._tasks.values() if t.target_app))

    @property
    def count(self) -> int:
        """Number of registered tasks."""
        return len(self._tasks)


def random_duration() -> int:
    """Generate a random duration in minutes."""
    return random.choice([15, 30, 45, 60, 90, 120])


def random_amount() -> str:
    """Generate a random expense amount."""
    return f"${random.randint(5, 500)}.{random.randint(0, 99):02d}"


def random_expense_category() -> str:
    """Generate a random expense category."""
    return random.choice(["Food", "Transport", "Office", "Travel", "Utilities", "Other"])


def random_note_title() -> str:
    """Generate a random note title."""
    topics = ["Project", "Meeting", "Ideas", "Tasks", "Notes", "Draft", "Memo"]
    return f"{random.choice(topics)}_{random.randint(100, 999)}"


def random_note_content() -> str:
    """Generate random note content."""
    templates = [
        "This is a test note about {topic}. Remember to {action}.",
        "Notes from today's {topic}: {action} by end of week.",
        "Quick memo: {topic} - {action}",
    ]
    topics = ["project update", "meeting agenda", "task list", "deadline", "review"]
    actions = ["follow up", "send email", "schedule call", "review docs", "update status"]
    return random.choice(templates).format(topic=random.choice(topics), action=random.choice(actions))


def random_date_offset() -> int:
    """Generate a random date offset in days (1-30 days from today)."""
    return random.randint(1, 30)


def random_time_hour() -> int:
    """Generate a random hour (9am - 6pm)."""
    return random.randint(9, 18)


def random_recipe_name() -> str:
    """Generate a random recipe name."""
    dishes = ["Pasta", "Salad", "Soup", "Stir-fry", "Curry", "Sandwich", "Tacos", "Pizza"]
    styles = ["Italian", "Asian", "Mexican", "Classic", "Spicy", "Veggie", "Quick"]
    return f"{random.choice(styles)} {random.choice(dishes)}"


def random_ingredient() -> str:
    """Generate a random ingredient list."""
    ingredients = ["tomatoes", "onions", "garlic", "olive oil", "salt", "pepper", "chicken", "rice"]
    return ", ".join(random.sample(ingredients, k=random.randint(3, 5)))


# --- NEW PARAM GENERATORS (Phase 0 expansion to 50 tasks) ---

def random_operator() -> str:
    """Generate a random arithmetic operator."""
    return random.choice(["+", "-", "×", "÷"])


def random_number() -> int:
    """Generate a random number for calculator tasks."""
    return random.randint(1, 999)


def random_angle() -> int:
    """Generate a random angle in degrees for scientific calculator."""
    return random.choice([0, 30, 45, 60, 90, 120, 180, 270, 360])


def random_share_app() -> str:
    """Generate a random app name for sharing."""
    return random.choice(["Messages", "Gmail", "Bluetooth", "Drive"])


def random_playlist_name() -> str:
    """Generate a random playlist name."""
    moods = ["Chill", "Workout", "Focus", "Party", "Relax", "Morning", "Night"]
    genres = ["Vibes", "Mix", "Beats", "Tunes", "Hits", "Jams"]
    return f"{random.choice(moods)} {random.choice(genres)}"


def random_location() -> str:
    """Generate a random location for maps search."""
    return random.choice([
        "Central Park", "Times Square", "Golden Gate Bridge",
        "Eiffel Tower", "Big Ben", "nearest coffee shop",
        "nearest gas station", "airport",
    ])


def random_destination() -> str:
    """Generate a random destination for directions."""
    return random.choice([
        "San Francisco Airport", "downtown", "nearest hospital",
        "Central Station", "the nearest pharmacy", "City Hall",
    ])


# --- FULL COVERAGE PARAM GENERATORS (expansion to 116 tasks) ---

def random_event_description() -> str:
    """Generate a random event description."""
    return random.choice([
        "Discuss project updates. Remember to bring notes.",
        "Weekly team sync - review progress and blockers.",
        "One-on-one with manager. Prepare status report.",
        "Review code changes and plan next sprint.",
        "We will understand software updates. Remember to confirm attendance.",
    ])


def random_event_duration() -> int:
    """Generate a random event duration in minutes."""
    return random.choice([15, 30, 45, 60, 90, 120])


def random_repeat_interval() -> str:
    """Generate a random repeat interval for calendar events."""
    return random.choice(["daily", "weekly", "biweekly", "monthly", "yearly"])


def random_day_of_week() -> str:
    """Generate a random day of the week."""
    return random.choice(["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])


def random_brightness_level() -> int:
    """Generate a random brightness level percentage."""
    return random.choice([0, 25, 50, 75, 100])


def random_screen_timeout() -> str:
    """Generate a random screen timeout value."""
    return random.choice(["15 seconds", "30 seconds", "1 minute", "2 minutes", "5 minutes", "10 minutes"])


def random_ringtone_volume() -> int:
    """Generate a random volume level 0-100."""
    return random.randint(0, 100)


def random_folder_name() -> str:
    """Generate a random folder name."""
    return f"Folder_{''.join(random.choices(string.ascii_lowercase, k=6))}"


def random_markor_file() -> str:
    """Generate a random Markor file name with extension."""
    name = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"{name}.txt"


def random_header_text() -> str:
    """Generate random header/footer text."""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=20))


def random_recipe_category() -> str:
    """Generate a random recipe category."""
    return random.choice(["Breakfast", "Lunch", "Dinner", "Dessert", "Snack", "Appetizer", "Drink"])


def random_recipe_instructions() -> str:
    """Generate random recipe instructions."""
    steps = [
        "Preheat oven to 350°F.",
        "Mix all ingredients in a large bowl.",
        "Cook on medium heat for 15 minutes.",
        "Stir occasionally until golden brown.",
        "Let cool for 5 minutes before serving.",
    ]
    return " ".join(random.sample(steps, k=random.randint(2, 4)))


def random_expense_csv() -> str:
    """Generate a random expense CSV entry."""
    cats = ["Food", "Transport", "Office", "Travel", "Utilities"]
    amt = f"{random.randint(5, 500)}.{random.randint(0, 99):02d}"
    return f"{random.choice(cats)},{amt},{random.choice(['cash', 'card', 'bank'])}"


def random_activity_type() -> str:
    """Generate a random sports activity type."""
    return random.choice(["Running", "Cycling", "Walking", "Hiking", "Swimming"])


def random_activity_distance() -> str:
    """Generate a random distance."""
    return f"{random.randint(1, 42)}.{random.randint(0, 9)} km"


def random_activity_duration_str() -> str:
    """Generate a random activity duration string."""
    h = random.randint(0, 3)
    m = random.randint(0, 59)
    return f"{h}h {m}m"


def random_task_title() -> str:
    """Generate a random task/todo title."""
    actions = ["Review", "Complete", "Submit", "Prepare", "Draft", "Update", "Schedule", "Fix"]
    subjects = ["report", "presentation", "budget", "code review", "meeting notes", "proposal", "test plan"]
    return f"{random.choice(actions)} {random.choice(subjects)}"


def random_task_priority() -> str:
    """Generate a random task priority."""
    return random.choice(["High", "Medium", "Low", "None"])


def random_due_date_str() -> str:
    """Generate a random due date string."""
    day = random.randint(1, 28)
    month = random.choice(["October", "November", "December"])
    return f"{month} {day}, 2023"


def random_notebook_name() -> str:
    """Generate a random notebook name."""
    topics = ["Work", "Personal", "Research", "Projects", "Archive", "Ideas", "Journal"]
    return f"{random.choice(topics)}_{random.randint(100, 999)}"


def random_joplin_content() -> str:
    """Generate random Joplin note content."""
    return random.choice([
        "Meeting notes: discussed roadmap and priorities.",
        "Research findings: need to investigate further.",
        "Quick thought: remember to follow up on this.",
        "Draft outline for the upcoming presentation.",
    ])


def random_media_file() -> str:
    """Generate a random media file name."""
    name = ''.join(random.choices(string.ascii_lowercase, k=6))
    ext = random.choice(["mp4", "mp3", "mkv", "avi", "ogg"])
    return f"{name}.{ext}"


def random_vlc_files() -> str:
    """Generate a list of media files for VLC playlist."""
    files = [f"track_{i}.mp3" for i in range(1, random.randint(3, 6))]
    return ", ".join(files)


def random_recording_name() -> str:
    """Generate a random audio recording name."""
    topics = ["meeting", "interview", "lecture", "memo", "note", "recording"]
    return f"{random.choice(topics)}_{random.randint(100, 999)}"


def random_drawing_name() -> str:
    """Generate a random drawing file name."""
    return f"drawing_{''.join(random.choices(string.ascii_lowercase, k=5))}"


def random_alarm_time() -> str:
    """Generate a random alarm time."""
    h = random.randint(5, 23)
    m = random.choice([0, 15, 30, 45])
    return f"{h:02d}:{m:02d}"


def random_alarm_label() -> str:
    """Generate a random alarm label."""
    return random.choice(["Wake up", "Medicine", "Meeting", "Workout", "Lunch", "Pick up kids"])

