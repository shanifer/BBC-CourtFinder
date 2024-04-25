import json
import traceback
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, time

import pandas as pd
import pytz
import streamlit as st
from streamlit.logger import get_logger

logger = get_logger(__name__)


PST_TIME_ZONE = 'America/Los_Angeles'
BELLEVUE_BADMINTON_CLUB_ORG_ID = 7031
CLUB_OPENING_HOURS = (6, 22)  # open, close hour
COURT_BOOKINGS_URL = 'https://memberschedulers.courtreserve.com/SchedulerApi/ReadExpandedApi'


############################################################################################
# Fetch data
############################################################################################
def fetch_court_times_data(court_date: datetime):
    headers = {
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9',
        'origin': 'https://app.courtreserve.com',
        'priority': 'u=1, i',
        'referer': 'https://app.courtreserve.com/',
        'sec-ch-ua': '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-site',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    }
    utc_datetime = court_date.astimezone(pytz.utc)
    params = {
        'id': str(BELLEVUE_BADMINTON_CLUB_ORG_ID),
        'uiCulture': 'en-US',
        'sort': '',
        'group': '',
        'filter': '',
        'jsonData': json.dumps({
            'startDate': utc_datetime.strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            'orgId': str(BELLEVUE_BADMINTON_CLUB_ORG_ID),
            'TimeZone': PST_TIME_ZONE,
            'Date': court_date.strftime('%a, %d %b %Y %H:%M:%S GMT'),
            'KendoDate': {'Year': court_date.year, 'Month': court_date.month, 'Day': court_date.day},
            'UiCulture': 'en-US',
            'CostTypeId': '88166',
            'CustomSchedulerId': '',  # Retrieves for all locations
            'ReservationMinInterval': '60',
            'SelectedCourtIds': '',  # Retrieves for all court numbers
            'SelectedInstructorIds': '',
            'MemberIds': '',
            'MemberFamilyId': '',
            'EmbedCodeId': '',
            'HideEmbedCodeReservationDetails': 'True'
        })
    }
    get_url = f"{COURT_BOOKINGS_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(get_url,
                                 headers=headers)
    with urllib.request.urlopen(req) as response:
        encoding = response.info().get_content_charset('utf-8')
        data = response.read()
        return json.loads(data.decode(encoding))['Data']


def get_available_court_times_by_location(court_date: datetime) -> dict:
    logger.info(f"Fetching reserved court times on {court_date}.")
    court_times = fetch_court_times_data(court_date)
    reserved_court_times_by_location = defaultdict(lambda: defaultdict(list))
    available_court_times_by_location = defaultdict(lambda: defaultdict(list))

    for item in court_times:
        court_location, court_number = get_court_location_and_number(item)
        reserved_court_times_by_location[court_location][court_number].append((get_reserved_court_start_end_times(item)))
        available_court_times_by_location[court_location][court_number] = []

    available_30min_intervals = generate_30min_intervals(
        get_datetime_by_hour(court_date, CLUB_OPENING_HOURS[0], PST_TIME_ZONE),
        get_datetime_by_hour(court_date, CLUB_OPENING_HOURS[1], PST_TIME_ZONE))

    for location, court_times_by_court_number in reserved_court_times_by_location.items():
        for court_number, reserved_court_times in court_times_by_court_number.items():
            available_court_times = []
            for i in range(len(available_30min_intervals) - 1):
                interval_start = available_30min_intervals[i]
                interval_end = available_30min_intervals[i + 1]
                is_available = True
                for reserved_start, reserved_end in reserved_court_times:
                    # Check if the interval is reserved
                    if interval_end <= reserved_start or interval_start >= reserved_end:
                        continue
                    else:
                        is_available = False
                        break
                if is_available:
                    available_court_times.append((interval_start, interval_end))
            available_court_times_by_location[location][court_number] = available_court_times
    return available_court_times_by_location


def get_court_location_and_number(item: dict):
    space_delimited_court_label = item["CourtLabel"].split(' ')
    court_location = space_delimited_court_label[0]
    court_number = space_delimited_court_label[1]
    court_label = f"Court {court_number}"
    return court_location, f"{court_label}"


def get_reserved_court_start_end_times(item: dict):
    start_utc = item["Start"][:-1]
    end_utc = item["End"][:-1]

    start_dt_utc = pytz.utc.localize(datetime.fromisoformat(start_utc))
    end_dt_utc = pytz.utc.localize(datetime.fromisoformat(end_utc))

    pst = pytz.timezone(PST_TIME_ZONE)

    return start_dt_utc.astimezone(pst), end_dt_utc.astimezone(pst)


############################################################################################
# UI Data Refreshes
############################################################################################
def update_compact_view_available_court_times():
    if not st.session_state.df_by_location:
        return

    start_time = st.session_state.time_range_filter[0]
    end_time = st.session_state.time_range_filter[1]

    logger.info(f"Creating a compact view for {st.session_state.locations_filter}, from {start_time} to {end_time}.")
    intervals = pd.date_range(start=start_time, end=end_time, freq='30min').strftime('%I:%M %p')
    compact_view_df = pd.DataFrame(index=intervals, columns=sorted(st.session_state.locations_filter))
    for location in st.session_state.locations_filter:
        single_location_df = st.session_state.df_by_location[location]
        for index in compact_view_df.index:
            available_courts = []
            for court_number in single_location_df.columns:
                if not pd.isnull(single_location_df.loc[index, court_number]):
                    available_courts.append(court_number)

            compact_view_df.at[index, location] = available_courts

    st.session_state.compact_view_df = compact_view_df


def update_available_courts_for_date():
    court_date = st.session_state.date_input_datetime
    available_court_times_by_location = get_available_court_times_by_location(court_date)
    st.session_state.bbc_locations = available_court_times_by_location.keys()
    for location, court_times_by_court_number in available_court_times_by_location.items():

        start_time, end_time = get_default_date_range_filter()
        intervals = pd.date_range(start=start_time, end=end_time, freq='30min')
        df = pd.DataFrame(index=intervals, columns=sorted(court_times_by_court_number.keys()))

        for court, times in court_times_by_court_number.items():
            for start, end in times:
                df.loc[(df.index >= start) & (df.index < end), court] = f"✓ {start.strftime('%I:%M %p')}"
        df.index = df.index.strftime('%I:%M %p')
        st.session_state.df_by_location[location] = df


############################################################################################
# UI Utils
############################################################################################
def get_duration_options(max_hours=4, increments_in_hours=0.5):
    duration_options = []
    for hour in range(1, int(max_hours / increments_in_hours) + 1):
        duration = hour * increments_in_hours
        duration_options.append(int(duration) if duration.is_integer() else duration)
    return duration_options


def display_time_range_picker():
    opening_time = get_time_by_hour(CLUB_OPENING_HOURS[0])
    closing_time = get_time_by_hour(CLUB_OPENING_HOURS[1])
    opening_datetime = to_pst_datetime(get_time_by_hour(CLUB_OPENING_HOURS[0]))
    closing_datetime = to_pst_datetime(get_time_by_hour(CLUB_OPENING_HOURS[1]))

    col1, col2 = st.columns(2)

    start_datetime = opening_datetime
    end_datetime = closing_datetime

    with col1:
        start_time = st.time_input("Start Time", opening_time, step=timedelta(minutes=30))
        start_datetime = to_pst_datetime(start_time)
    with col2:
        end_time_or_duration = st.radio("End time or Duration", ["End Time", "Duration"],
                 captions=["Find open courts from [Start Time] to [End Time]",
                           "Find open courts for [Duration] starting at [Start Time]"],
                 horizontal=True, )
        if end_time_or_duration == "End Time":
            end_time = st.time_input("End Time", closing_time, step=timedelta(minutes=30))
            end_datetime = to_pst_datetime(end_time)
        else:
            duration = st.selectbox("Duration (in hours)", options=get_duration_options(), index=None)
            if duration:
                end_datetime = start_datetime + timedelta(hours=duration)

    if end_datetime < start_datetime:
        st.warning("End time cannot be before start time. Please select a valid end time.")
    elif start_datetime < opening_datetime or end_datetime > closing_datetime:
        st.warning(f"You're trying to look for courts from {get_formatted_time((start_datetime))} to {get_formatted_time(end_datetime)}. "
                   f"Please select times/duration that falls between Opening ({get_formatted_time(opening_time)}) and Closing ({get_formatted_time(closing_time)}).")
    else:
        st.session_state.time_range_filter = (start_datetime, end_datetime)


############################################################################################
# Util
############################################################################################
def get_default_date_range_filter() -> tuple[datetime, datetime]:
    start_time = get_datetime_by_hour(st.session_state.date_input_datetime, CLUB_OPENING_HOURS[0], PST_TIME_ZONE)
    end_time = get_datetime_by_hour(st.session_state.date_input_datetime, CLUB_OPENING_HOURS[1], PST_TIME_ZONE)
    return start_time, end_time


def get_datetime_by_hour(date: datetime, hour: int, timezone: str):
    return pytz.timezone(timezone).localize(datetime.combine(date, time(hour=hour)))


def generate_30min_intervals(start_time: datetime, end_time: datetime):
    intervals = []
    current_time = start_time
    while current_time < end_time:
        intervals.append(current_time)
        current_time += timedelta(minutes=30)
    return intervals


def get_default_datetime():
    current_datetime = datetime.now(pytz.timezone(PST_TIME_ZONE))
    if current_datetime.time() > time(21, 30):  # Latest court time is 9:30 PM
        return current_datetime + timedelta(days=1)
    return current_datetime


def to_pst_datetime(utc_time: time):
    return pytz.timezone(PST_TIME_ZONE).localize(datetime.combine(st.session_state.date_input_datetime, utc_time))


def get_formatted_time(time_to_format):
    return time_to_format.strftime('%I:%M %p')


def get_formatted_time_by_hour(hour: int):
    return get_formatted_time(get_time_by_hour(hour))


def get_time_by_hour(hour: int):
    return time(hour, 0)


############################################################################################
# Main App
############################################################################################
def main():
    try:
        st.set_page_config(page_title="BBC Court Finder", page_icon=":badminton_racquet_and_shuttlecock:", layout='wide')

        hide_menu_style = """
            <style>
                #MainMenu {visibility: hidden;}
                footer {visibility: hidden;}
            </style>
            """
        st.markdown(hide_menu_style, unsafe_allow_html=True)

        st.title("Find Available Courts @ BBC")

        # Initialize session states
        # (Streamlit reloads page on every input)
        if 'locations_filter' not in st.session_state:
            st.session_state.locations_filter = []
        if 'time_range_filter' not in st.session_state:
            st.session_state.time_range_filter = ()
        if 'date_input_datetime' not in st.session_state:
            st.session_state.date_input_datetime = None
        if 'df_by_location' not in st.session_state:
            st.session_state.df_by_location = {}
        if 'compact_view_df' not in st.session_state:
            st.session_state.compact_view_df = None
        if 'bbc_locations' not in st.session_state:
            st.session_state.bbc_locations = []

        current_datetime = get_default_datetime()
        date_input = st.date_input("Date", current_datetime,
                                   max_value=current_datetime + timedelta(days=30))

        st.session_state.date_input_datetime = pytz.timezone(PST_TIME_ZONE).localize(datetime.combine(date_input, datetime.min.time()))

        display_time_range_picker()

        update_available_courts_for_date()

        if st.session_state.bbc_locations:
            st.session_state.locations_filter = st.multiselect("Locations",
                                                               placeholder="Choose a location",
                                                               options=st.session_state.bbc_locations,
                                                               default=st.session_state.bbc_locations)
        st.divider()

        update_compact_view_available_court_times()

        if st.session_state.compact_view_df is not None:
            st.write(f"### Available courts from {get_formatted_time(st.session_state.time_range_filter[0])} to "
                     f"{get_formatted_time(st.session_state.time_range_filter[1])} for {', '.join(st.session_state.locations_filter)}")
            with st.expander("How do I read this?"):
                st.write("- This is a filtered/compact view of the tables in the next section showing court-availability across locations.")
                st.write("- Each column is a BBC location.")
                st.write("- Each row lists the courts that should be open for reservation on CourtReserve at that starting time.")

            st.dataframe(st.session_state.compact_view_df)
            st.divider()

        if st.session_state.df_by_location:
            st.write(f"### Available courts from Opening ({get_formatted_time_by_hour(CLUB_OPENING_HOURS[0])}) to "
                     f"Close ({get_formatted_time_by_hour(CLUB_OPENING_HOURS[1])})")
            with st.expander("How do I read this?"):
                st.write("- These are the non-filtered court-availability views that should resemble the CourtReserve page when you click into specific locations under 'Reservations'.")
                st.write("- ✓ 07:00 AM - means a court should be open for reservation on CourtReserve with starting time at 07:00 AM.")
                st.write("- None - means the court is not available to be reserved at that time slot.")
            for location, df in st.session_state.df_by_location.items():
                st.write(f"#### :green[{location}]")
                st.dataframe(df)

    except Exception as e:
        logger.error(f"{type(e).__name__}: {str(e)}")
        logger.error(traceback.format_exc())  # Print the full traceback
        st.error("Oops, something went wrong.")


if __name__ == "__main__":
    main()
