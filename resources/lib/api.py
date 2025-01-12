# -*- coding: utf-8 -*-
# Crunchyroll
# Copyright (C) 2018 MrKrabat
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import json as JSON
from datetime import timedelta
from typing import Optional, Dict

import requests
import xbmc
import xbmcvfs

from . import utils
from .model import AccountData, Args


class API:
    """Api documentation
    https://github.com/CloudMax94/crunchyroll-api/wiki/Api
    """
    URL = "https://api.crunchyroll.com/"
    VERSION = "1.1.21.0"
    TOKEN = "LNDJgOit5yaRIWN"
    DEVICE = "com.crunchyroll.windows.desktop"
    TIMEOUT = 30

    INDEX_ENDPOINT = "https://beta-api.crunchyroll.com/index/v2"
    PROFILE_ENDPOINT = "https://beta-api.crunchyroll.com/accounts/v1/me/profile"
    TOKEN_ENDPOINT = "https://beta-api.crunchyroll.com/auth/v1/token"
    SEARCH_ENDPOINT = "https://beta-api.crunchyroll.com/content/v1/search"
    STREAMS_ENDPOINT = "https://beta-api.crunchyroll.com/cms/v2{}/videos/{}/streams"
    SERIES_ENDPOINT = "https://beta-api.crunchyroll.com/cms/v2{}/series/{}"
    SEASONS_ENDPOINT = "https://beta-api.crunchyroll.com/cms/v2{}/seasons"
    EPISODES_ENDPOINT = "https://beta-api.crunchyroll.com/cms/v2{}/episodes"
    SIMILAR_ENDPOINT = "https://beta-api.crunchyroll.com/content/v1/{}/similar_to"
    NEWSFEED_ENDPOINT = "https://beta-api.crunchyroll.com/content/v1/news_feed"
    BROWSE_ENDPOINT = "https://beta-api.crunchyroll.com/content/v1/browse"
    # there is also a v2, but that will only deliver content_ids and no details about the entries
    WATCHLIST_LIST_ENDPOINT = "https://beta-api.crunchyroll.com/content/v1/{}/watchlist"
    # only v2 will allow removal of watchlist entries.
    # !!!! be super careful and always provide a content_id, or it will delete the whole playlist! *sighs* !!!!
    WATCHLIST_REMOVE_ENDPOINT = "https://beta-api.crunchyroll.com/content/v2/{}/watchlist/{}"
    WATCHLIST_ADD_ENDPOINT = "https://beta-api.crunchyroll.com/content/v2/{}/watchlist"
    PLAYHEADS_ENDPOINT = "https://beta-api.crunchyroll.com/content/v2/{}/playheads"
    HISTORY_ENDPOINT = "https://beta-api.crunchyroll.com/content/v2/{}/watch-history"
    SEASONAL_TAGS_ENDPOINT = "https://beta-api.crunchyroll.com/content/v2/discover/seasonal_tags"
    CATEGORIES_ENDPOINT = "https://beta-api.crunchyroll.com/content/v1/tenant_categories"

    AUTHORIZATION = "Basic aHJobzlxM2F3dnNrMjJ1LXRzNWE6cHROOURteXRBU2Z6QjZvbXVsSzh6cUxzYTczVE1TY1k="

    def __init__(
            self,
            args: Args = None,
            locale: str = "en-US"
    ) -> None:
        self.http = requests.Session()
        self.locale: str = locale
        self.account_data: AccountData = AccountData(dict())
        self.api_headers: Dict = utils.headers()
        self.args = args
        self.retry_counter = 0

    def start(self) -> bool:
        session_restart = getattr(self.args, "session_restart", False)

        # restore account data from file
        session_data = self.load_from_storage()
        if session_data and not session_restart:
            self.account_data = AccountData(session_data)
            account_auth = {"Authorization": f"{self.account_data.token_type} {self.account_data.access_token}"}
            self.api_headers.update(account_auth)

            # check if tokes are expired
            if utils.get_date() > utils.str_to_date(self.account_data.expires):
                session_restart = True
            else:
                return True

        # session management
        self.create_session(session_restart)

        return True

    def create_session(self, refresh=False) -> None:
        # get login information
        username = self.args.addon.getSetting("crunchyroll_username")
        password = self.args.addon.getSetting("crunchyroll_password")

        headers = {"Authorization": API.AUTHORIZATION}
        data = {}

        if not refresh:
            data = {
                "username": username,
                "password": password,
                "grant_type": "password",
                "scope": "offline_access",
            }
        elif refresh:
            data = {
                "refresh_token": self.account_data.refresh_token,
                "grant_type": "refresh_token",
                "scope": "offline_access",
            }

        r = self.http.request(
            method="POST",
            url=API.TOKEN_ENDPOINT,
            headers=headers,
            data=data
        )

        # if refreshing and refresh token is expired, it will throw a 400
        # retry with a fresh login, but limit retries to prevent loop in case something else went wrong
        if r.status_code == 400:
            utils.crunchy_log(self.args, "Invalid/Expired credentials, restarting session from scratch")
            self.retry_counter = self.retry_counter + 1
            self.delete_storage()
            if self.retry_counter > 2:
                utils.crunchy_log(self.args, "Max retries exceeded. Aborting!", xbmc.LOGERROR)
                return None
            return self.create_session()

        r_json = utils.get_json_from_response(r)

        self.api_headers.clear()
        self.account_data = AccountData({})

        access_token = r_json["access_token"]
        token_type = r_json["token_type"]
        account_auth = {"Authorization": f"{token_type} {access_token}"}

        account_data = dict()
        account_data.update(r_json)
        self.account_data = AccountData({})
        self.api_headers.update(account_auth)

        r = self.make_request(
            method="GET",
            url=API.INDEX_ENDPOINT
        )
        account_data.update(r)

        r = self.make_request(
            method="GET",
            url=API.PROFILE_ENDPOINT
        )
        account_data.update(r)

        account_data["expires"] = utils.date_to_str(
            utils.get_date() + timedelta(seconds=float(account_data["expires_in"])))
        self.account_data = AccountData(account_data)

        self.write_to_storage(self.account_data)
        self.retry_counter = 0

    def close(self) -> None:
        """Saves cookies and session
        """
        # no longer required, data is saved upon session update already

    def destroy(self) -> None:
        """Destroys session
        """
        self.delete_storage()

    def make_request(
            self,
            method: str,
            url: str,
            headers=None,
            params=None,
            data=None,
            json=None
    ) -> Optional[Dict]:
        if params is None:
            params = dict()
        if headers is None:
            headers = dict()
        if self.account_data:
            if expiration := self.account_data.expires:
                current_time = utils.get_date()
                if current_time > utils.str_to_date(expiration):
                    self.create_session(refresh=True)
            params.update({
                "Policy": self.account_data.cms.policy,
                "Signature": self.account_data.cms.signature,
                "Key-Pair-Id": self.account_data.cms.key_pair_id
            })
        headers.update(self.api_headers)

        r = self.http.request(
            method,
            url,
            headers=headers,
            params=params,
            data=data,
            json=json
        )
        return utils.get_json_from_response(r)

    def get_storage_path(self) -> str:
        """Get cookie file path
        """
        profile_path = xbmcvfs.translatePath(self.args.addon.getAddonInfo("profile"))

        return profile_path

    def load_from_storage(self) -> Optional[Dict]:
        storage_file = self.get_storage_path() + "session_data.json"

        if not xbmcvfs.exists(storage_file):
            return None

        with xbmcvfs.File(storage_file) as file:
            data = JSON.load(file)

        d = dict()
        d.update(data)

        return d

    def delete_storage(self) -> None:
        storage_file = self.get_storage_path() + "session_data.json"

        if not xbmcvfs.exists(storage_file):
            return None

        xbmcvfs.delete(storage_file)

    def write_to_storage(self, account: AccountData) -> bool:
        storage_file = self.get_storage_path() + "session_data.json"

        # serialize (Object has a to_str serializer)
        json_string = str(account)

        with xbmcvfs.File(storage_file, 'w') as file:
            result = file.write(json_string)

        return result
