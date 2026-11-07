# Nest Legacy Integration for Home Assistant

![Nest Legacy Header](https://brands.home-assistant.io/nest/logo.png)

[![hacs_badge](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/integration)

This is a custom component for Home Assistant to integrate a wide range of Nest devices using an unofficial web API (combining REST streaming and Protobuf). It serves as an alternative to the official Nest integration, providing support for devices not available through Google's official Smart Device Management (SDM) API, such as the Nest Protect, Nest x Yale Lock, and Nest Heat Link.

This integration provides real-time updates for most sensors and controls by maintaining persistent connections to the Nest API.

## Why use Nest Legacy?

The official Home Assistant Nest integration uses Google's Smart Device Management (SDM) API, which has a limited set of supported devices and requires a one-time $5 fee. This **Nest Legacy** integration uses the same unofficial APIs that the Nest mobile and web apps use, offering several key advantages.

### Comparison with the Official Nest Integration

| Feature                  | Nest Legacy (This Integration)                                  | Official Nest Integration (SDM API)                            |
| ------------------------ | --------------------------------------------------------------- | -------------------------------------------------------------- |
| **API Used**             | Unofficial Nest Web API (REST & Protobuf)                       | Official Google SDM API                                        |
| **Cost**                 | Free                                                            | $5 USD one-time fee required by Google                         |
| **Authentication**       | Access Token (Nest Account) or Cookies (Google Account)         | OAuth2 with Google Cloud Project                               |
| **Supported Devices**    | Wider range, including **Nest Protect**, **Nest Temp Sensors**, **Nest x Yale Locks**, and **Nest Heat Link**. | Limited to newer Thermostats, Cameras, and Doorbells.         |
| **Data Updates**         | Push-based (Subscriber/Observer)                                | Push-based (Pub/Sub)                                           |
| **Stability**            | Relies on an unofficial API that could change or break without notice. | Officially supported by Google, more stable long-term.        |

**In short, use this integration if you want to:**

- Integrate Nest Protects, Temperature Sensors, Locks, or Heat Links.
- Avoid the $5 Google API fee.
- Access features not exposed by the official API (e.g., Protect component health tests, Heat Link boost, controlling Thermostat via specific Temp Sensors).

## Supported Devices

This integration supports a wide variety of Nest devices:

- **Nest Thermostats** (1st, 2nd, 3rd gen, Thermostat E, 2020 mirror edition)
- **Nest Protect** (1st and 2nd gen, both wired and battery)
- **Nest Temperature Sensors** (Kryptonite)
- **Nest Cameras** (Cam Indoor, IQ Indoor, Outdoor, IQ Outdoor)
- **Nest Doorbells** (Wired 1st gen)
- **Nest x Yale Locks**
- **Nest Heat Link** (for UK/EU hot water control)

## Features & Entities

This integration creates a rich set of entities for your Nest devices based on their capabilities.

### Nest Thermostat

- **Climate:** Full control over temperature, HVAC modes (Heat, Cool, Heat/Cool, Off), and Presets (None, Eco). Supports Target Humidity if a humidifier/dehumidifier is present.
- **Fan:** Independent control of the fan (On/Off, Speed/Percentage). You can also run the fan for a specific duration using the `nest_legacy.set_fan_timer` action.
- **Sensors:** Current Temperature, Target Temperature, Humidity, Target Humidity, Backplate Temperature, Filter Runtime, HVAC Stage (which heating or cooling stage is running).
- **Binary Sensors:** Occupancy, Leaf status (Eco indicator), Filter Replacement Needed.
- **Switches:** Temperature Lock, Dehumidifier State.
- **Number:** Dual Fuel Breakpoint, the outdoor temperature below which the alternate heat source is used instead of the heat pump. Only on dual fuel systems.
- **Select:** Dual Fuel Breakpoint Override (None, Always alternate heat, Never alternate heat). Only on dual fuel systems.

### Nest Temperature Sensor

- **Sensors:** Current Temperature, Battery Level.
- **Switch:** **Control Thermostat** (Active Sensor). Turning this switch on forces the associated thermostat to use this sensor's reading for climate control.

### Nest Protect

- **Binary Sensors:** Smoke Status, CO Status, Heat Status.
- **Diagnostic Binary Sensors:** Battery Health, Line Power (wired only), Occupancy (wired only), Removed from Base status.
- **Component Tests:** Sensors indicating pass/fail for Speaker, Smoke, CO, WiFi, LED, PIR, Buzzer, and Humidity sensors.
- **Sensors:** Battery Level (%), Replace By Date, Last Manual Test time, Last Audio Self-Test time.
- **Switches:** Nightly Promise (Green LED), Heads-Up Alerts, Steam Check, Night Light enable.
- **Select:** Night Light Brightness (Low, Medium, High).

### Nest Cameras & Doorbells

- **Camera:** Live streaming entity.
- **Switches:** Streaming Enabled, Audio Recording, Indoor Chime (Doorbell), Visitor Announcements (Doorbell), Night Vision (IR), Status LED, Video Rotation.
- **Events:** `event` entities that trigger on Motion, Person, Sound, Face Detection, and Doorbell Chime.
- **Media Browser:** Browse, play, and see thumbnails for historical camera events directly in the Home Assistant Media Browser.

### Nest x Yale Lock

- **Lock:** Lock and unlock control.
- **Sensors:** Battery Level, Last Actor (who locked/unlocked: Keypad, Manual, Remote, Voice, etc.).
- **Binary Sensor:** Tamper detection.
- **Switch:** Auto-Relock enable/disable.
- **Number:** Configure the Auto-Relock duration (seconds).

### Nest Heat Link (Europe)

- **Water Heater:** Control hot water heating.
- **Operation Modes:** Supports `off`, `schedule`, and several boost durations (`boost`, `boost_30m`, `boost_1h`, `boost_2h`).
- **Boost Mode:** Activates hot water for a specified duration (default 30 minutes for `boost`). The reported operation mode will dynamically update to reflect the remaining boost time (e.g., switching from `Boost (2h)` to `Boost (1h)` as time passes). Once the boost timer expires, the device automatically reverts to the previous mode (e.g., `schedule`).
- **Features:** Set target temperature (only when the Heat Link has a hot water sensor).
- **Switch:** Home/Away Assist, the Nest setting that lets Home/Away Assist switch hot water off while you are away.
- **Attributes:** `boiler_active`, `hot_water_schedule_active`, `away_active` (whether Home/Away Assist is suppressing hot water right now), `next_transition_time` (when the schedule next changes), and `boost_timer_end` while a boost runs.
- **Automation:** You can trigger a boost via automation using the `water_heater.set_operation_mode` action:

  ```yaml
  automation:
    triggers:
      - trigger: time
        at: "07:15:00"
    actions:
      - action: water_heater.set_operation_mode
        target:
          entity_id: water_heater.nest_heat_link
        data:
          operation_mode: boost_1h # Boost for 1 hour
  ```

### Structure (Home)

- **Select:** Set the structure mode (Home, Away, Sleep, Vacation).

## Custom Actions

This integration provides several custom actions for advanced functionality.

### `nest_legacy.list_guests`

Lists all guests configured on your Nest structures. This action returns a list of guests, which can be viewed in the Home Assistant trace or used in scripts with [response data](https://www.home-assistant.io/docs/scripts/perform-actions/#use-templates-to-handle-response-data).

- **Data:**
  - `config_entry_id` (Optional): The config entry of the Nest Legacy integration. Required if you have multiple Nest Legacy entries.

### `nest_legacy.get_user_schedule`

Gets the access schedule for a specific user on a lock. This action returns the schedule details.

- **Data:**
  - `device_id` (Required): The lock device to target.
  - `user_id` (Required): The user or guest resource ID (e.g., `GUEST_1234`).

### `nest_legacy.set_user_schedule`

Sets the access schedule for a user on a lock.

- **Data:**
  - `device_id` (Required): The lock device to target.
  - `user_id` (Required): The user or guest resource ID (e.g., `GUEST_1234`).
  - `days_of_week` (Optional): The days of the week when access is allowed (e.g., `monday`, `tuesday`).
  - `start_time` (Optional): The time of day when access starts (e.g., `14:00:00`).
  - `duration` (Optional): The length of the daily access window (e.g., `04:00:00`).
  - `start_timebox` (Optional): The date and time when access begins.
  - `end_timebox` (Optional): The date and time when access expires.

### `nest_legacy.delete_user_schedule`

Deletes the access schedule for a user on a device.

- **Data:**
  - `device_id` (Required): The lock device to target.
  - `user_id` (Required): The user or guest resource ID (e.g., `GUEST_1234`).

### `nest_legacy.set_fan_timer`

Sets the thermostat fan to run for a specific duration.

- **Target:** The `climate` entity of your Nest Thermostat.
- **Data:**
  - `duration` (Required): The length of time the fan should run (e.g., `00:15:00` for 15 minutes).

## Installation

### HACS

This integration is included in the default HACS repository.

1. Open HACS in Home Assistant.
2. Search for "Nest Legacy" in the Integrations section and click download.
3. Restart Home Assistant.
4. Go to Settings > Devices & Services > Add Integration > Search for "Nest Legacy".

## Configuration

After installation, the integration can be configured via the Home Assistant UI.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=nest_legacy)

You will be asked to select your account type. Follow the instructions below based on your account.

### Option A: Google Account

For accounts migrated to Google, or created after August 2019. You will need to copy an `issue_token` and `cookies` from your browser.

⚠️ **Use Firefox.** Do **not** use Google Chrome, Microsoft Edge or any other Chromium-based browser, not even in Incognito mode. They bind Google's sign-in session to your device ([Device Bound Session Credentials](https://developer.chrome.com/docs/web-platform/device-bound-session-credentials)), so credentials copied out of them work at first and then stop working within a few hours. Safari also works, but only with the manual method.

**Sign in fresh, copy the values, then close the window without signing out.** A Firefox private window is the easiest way to do this: closing it throws away the browser's copy of the session without signing you out of Google. A browser window that stays signed in keeps using the same session, and signing out of `home.nest.com` or Google invalidates the credentials immediately.

Once set up, the integration saves the cookies Google rotates while it runs, so the credentials survive Home Assistant restarts.

#### Recommended Method: Nest Token Extractor

The **[Nest Token Extractor](https://github.com/tronikos/nest-token-extractor)** browser extension captures the credentials for you.

1. [Install the extension in Firefox](https://github.com/tronikos/nest-token-extractor#mozilla-firefox). Use v1.0.7 or newer; older versions could pick up the cookies of your regular Firefox windows while extracting from a private window.
2. Open a private window (Ctrl+Shift+P, or Cmd+Shift+P on macOS). If the extension icon is missing there, go to `about:addons`, open **Nest Token Extractor** and set **Run in Private Windows** to **Allow**.
3. In the private window, click the extension icon, choose your environment, and click **Open Nest & Start Extraction**.
4. Sign in with Google and wait for the green checkmark on the extension icon.
5. Click the extension icon again and copy the **Issue Token** and **Cookies** into Home Assistant.
6. Close the private window. Do not sign out.

If the **Cookies** field stays empty, click the **Shield** icon in the address bar on `home.nest.com`, turn off **Enhanced Tracking Protection**, and restart the extraction.

#### Manual Method

(Instructions adapted from the `homebridge-nest` project).

1. Open a Firefox private window (Ctrl+Shift+P, or Cmd+Shift+P on macOS).
2. Open Developer Tools (F12) and select the **Network** tab. In its settings (gear icon), check **Persist Logs**.
3. Go to `home.nest.com`. Click the **Shield** icon in the address bar and turn off **Enhanced Tracking Protection** for the site. Private windows use strict tracking protection, which can block the Google sign-in frame and leave you in a sign-in loop.
4. Click **Sign in with Google** and sign in.
5. In the Network filter box, enter `issueToken`. One request, `iframerpc`, will appear.
6. Right-click it and choose **Copy Value** > **Copy URL**. This is your `Issue token`.
7. Click the request and, in the **Headers** panel under **Request Headers**, right-click the `Cookie` header and choose **Copy Value**. This is your `Cookies`. Don't select the text by hand, since that can cut it short. It must include `SID=`.
8. Paste both values into the Home Assistant configuration form.
9. Close the private window. Do not sign out.

In Safari, enable **Settings** > **Advanced** > **Show features for web developers**, open a private window and use **Develop** > **Show Web Inspector** with **Preserve Log** checked. The `iframerpc` request shows the URL under **Summary** and the `Cookie` header under **Request**. Quit Safari when you are done, without signing out.

### Option B: Legacy Nest Account

For older, non-migrated Nest accounts. You will need to obtain an `access_token`. The [Nest Token Extractor](https://github.com/tronikos/nest-token-extractor) can also get it for you, in any browser.

1. Go to `https://home.nest.com` in your browser and log in.
2. Once logged in, open a new tab and go to `https://home.nest.com/session`.
3. You will see a long string of text. Find `"access_token": "..."` near the beginning.
4. Copy the value inside the quotes (it's a long sequence of letters, numbers and punctuation beginning with `b`). This is your `Access token`.
5. Paste this value into the Home Assistant configuration form.
6. **Do not log out of `home.nest.com`**, as this will invalidate your credentials. Just close the browser tab.

### Field Test Environment

If you are part of the Google Field Test program, check the "Use Field Test environment" box during setup.

### Configuration Options

Once set up, you can click "Configure" on the integration entry to tweak settings:

- **Camera Event Poll Interval:** How often to check for new camera events (default: 5 seconds). Set it to 0 to disable polling.
- **Protobuf Options:** Enable/Disable the use of the newer Protobuf API for specific device types (Protects, Structure, Cameras). Locks always use the Protobuf API; Thermostats, Temperature Sensors and Heat Links use it when available and fall back to the REST API.

## Troubleshooting

- **Authentication Errors:** If you receive authentication errors, your cookies or tokens may have expired. You will need to re-fetch them using the steps above and use the "Reconfigure" option in the integration.
- **Credentials stop working after a few hours:** They were most likely copied from Chrome or Edge, or from a browser window that stayed signed in. Get them again in a Firefox private window, following the steps above, and close the window right after.
- **"No active session found" / Invalid authentication on setup:** If your debug logs show `BadCredentialsException('No active session found.')`, the cookies you provided did not contain a valid Google sign-in session. Usually the cookie value was cut short while copying (use **Copy Value**), was copied from a different request than `iframerpc`, or Enhanced Tracking Protection blocked the Google sign-in frame. Turn off Enhanced Tracking Protection for `home.nest.com` and try again, or use the **Nest Token Extractor** extension.
- **Missing Devices:** Ensure your devices are visible in the Nest app. Some newer Google Nest devices (like the 2021+ battery cameras) are exclusively on the Google Home app and may not appear here, or may have limited functionality via the legacy API.

## Credits

This integration would not be possible without the extensive research and work done by these projects:

- <https://github.com/chrisjshull/homebridge-nest>
- <https://github.com/n0rt0nthec4t/homebridge-nest-accfactory>
- <https://github.com/iMicknl/ha-nest-protect>

## Screenshots

### Configuration Screenshots

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/92bf01b3-98b6-4a01-9e6e-5e6e22dc1ca7" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/cae9c9ea-317e-4823-b7d0-744982d51ee4" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/5ad4ed27-85e2-4eca-b892-5c4f13f56aef" />

### Nest Protect Screenshot

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/e1e42e58-78be-4d26-b8aa-08a124703d58" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/571b9e65-c6c7-422c-8482-e3c8a3319992" />

### Nest Thermostat Screenshot

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/88a1082e-e38e-4a3e-95cd-59018ce383be" />

### Nest Camera Screenshot

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/d353b7de-c538-4fa6-bb0c-736377cd8419" />

### Nest Doorbell Screenshot

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/eb2c978f-ba9c-4c75-a726-b5db7e2660ae" />

### Nest Lock Screenshot

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/c7e248b7-be6f-479f-9475-b70f67ea1939" />

## Home Screenshot

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/024763eb-bd52-4810-a4b7-8bcae33a1d73" />

## Media Browser Screenshot

<img width="30%" alt="image" src="https://github.com/user-attachments/assets/6dd41a07-c59b-485c-8c86-681e561f50cc" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/b7acb8c7-9b5e-47ca-afd2-90b6034daa60" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/967406e1-42cf-4cf9-bf1b-bd6d7e4ba350" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/87f2d922-959d-4007-b715-3bd1aacb935b" />
<img width="30%" alt="image" src="https://github.com/user-attachments/assets/7955d2eb-c72b-485b-96e6-b05772b73970" />

## Disclaimer

This is a personal hobby project and is not affiliated with Google or Nest. It uses an unofficial API that could be changed or discontinued by Google at any time, which may cause this integration to stop working. It is provided "as-is," with no warranty whatsoever. Use at your own risk.
