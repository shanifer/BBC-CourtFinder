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

court_bookings_url = 'https://memberschedulers.courtreserve.com/SchedulerApi/ReadExpandedApi'


def get_headers():
    return {
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


def get_request_params(court_date: datetime):
    utc_datetime = court_date.astimezone(pytz.utc)
    return {
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


def fetch_court_times_data(court_date: datetime):
    get_url = f"{court_bookings_url}?{urllib.parse.urlencode(get_request_params(court_date))}"
    req = urllib.request.Request(get_url,
                                 headers=get_headers())
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


def get_datetime_by_hour(date: datetime, hour: int, timezone: str):
    return pytz.timezone(timezone).localize(datetime.combine(date, time(hour=hour)))


def generate_30min_intervals(start_time: datetime, end_time: datetime):
    intervals = []
    current_time = start_time
    while current_time < end_time:
        intervals.append(current_time)
        current_time += timedelta(minutes=30)
    return intervals


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


def get_default_datetime():
    current_datetime = datetime.now(pytz.timezone(PST_TIME_ZONE))
    if current_datetime.time() > time(21, 30):  # Latest court time is 9:30 PM
        return current_datetime + timedelta(days=1)
    return current_datetime


def get_duration_options(max_hours=4, increments_in_hours=0.5):
    duration_options = []
    for hour in range(1, int(max_hours / increments_in_hours) + 1):
        duration = hour * increments_in_hours
        duration_options.append(int(duration) if duration.is_integer() else duration)
    return duration_options


def display_time_range_picker():
    opening_time = time(CLUB_OPENING_HOURS[0], 0)
    closing_time = time(CLUB_OPENING_HOURS[1], 0)
    opening_datetime = to_datetime_based_on_date_input(opening_time)
    closing_datetime = to_datetime_based_on_date_input(closing_time)
    st.session_state.time_range_filter = (opening_datetime, closing_datetime)

    col1, col2 = st.columns(2)
    with col1:
        start_time = st.time_input("Start Time", opening_time, step=timedelta(minutes=30))
    with col2:
        end_time = st.time_input("End Time", closing_time, step=timedelta(minutes=30))
        st.write("OR")
        duration = st.selectbox("Duration (in hours)", options=get_duration_options(), index=None)

    start_datetime = to_datetime_based_on_date_input(start_time)
    end_datetime = to_datetime_based_on_date_input(end_time)
    if duration:
        st.session_state.disable_end_time_input = True
        end_datetime = start_datetime + timedelta(hours=duration)

    if end_datetime < start_datetime:
        st.warning("End time cannot be before start time. Please select a valid end time.")
    elif start_datetime < opening_datetime or end_datetime > closing_datetime:  # check if end time is within range of opening hours.
        st.warning(f"Please select times/duration that falls between {opening_time.strftime('%I:%M %p')} and {closing_time.strftime('%I:%M %p')}")
    else:
        st.session_state.time_range_filter = (start_datetime, end_datetime)


def to_datetime_based_on_date_input(time: time):
    return pytz.timezone(PST_TIME_ZONE).localize(datetime.combine(st.session_state.date_input_datetime, time))


def update_compact_view_available_court_times():
    if not st.session_state.df_by_location:
        return

    filtered_locations = st.session_state.locations_filter if st.session_state.locations_filter \
        else st.session_state.bbc_locations

    start_time = st.session_state.time_range_filter[0]
    end_time = st.session_state.time_range_filter[1]

    logger.info(f"Creating a compact view for {filtered_locations}, from {start_time} to {end_time}.")
    intervals = pd.date_range(start=start_time, end=end_time, freq='30min').strftime('%I:%M %p')
    compact_view_df = pd.DataFrame(index=intervals, columns=sorted(filtered_locations))
    for location in filtered_locations:
        single_location_df = st.session_state.df_by_location[location]
        for index in compact_view_df.index:
            available_courts = []
            for court_number in single_location_df.columns:
                if not pd.isnull(single_location_df.loc[index, court_number]):
                    available_courts.append(court_number)

            compact_view_df.at[index, location] = available_courts

    st.session_state.compact_view_df = compact_view_df


def get_default_date_range_filter() -> (datetime, datetime):
    start_time = get_datetime_by_hour(st.session_state.date_input_datetime, CLUB_OPENING_HOURS[0], PST_TIME_ZONE)
    end_time = get_datetime_by_hour(st.session_state.date_input_datetime, CLUB_OPENING_HOURS[1], PST_TIME_ZONE)
    return start_time, end_time


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
                                                               options=st.session_state.bbc_locations, default=[])
        st.divider()

        update_compact_view_available_court_times()

        if st.session_state.compact_view_df is not None:
            st.dataframe(st.session_state.compact_view_df)
            st.divider()

        if st.session_state.df_by_location:
            for location, df in st.session_state.df_by_location.items():
                st.write(f"#### :green[{location}]")
                st.dataframe(df)

    except Exception as e:
        logger.error(f"{type(e).__name__}: {str(e)}")
        logger.error(traceback.format_exc())  # Print the full traceback
        st.error("Oops, something went wrong.")


if __name__ == "__main__":
    main()
