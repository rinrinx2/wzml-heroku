from hashlib import sha1
from base64 import b16encode, b32decode
from bencoding import bencode, bdecode
from time import sleep, time
from re import search as re_search
from os import remove
from os import path as ospath, listdir
from time import sleep, time
from re import search as re_search
from threading import Lock, Thread

from bot import TELEGRAPH_STYLE, download_dict, download_dict_lock, BASE_URL, get_client, TORRENT_DIRECT_LIMIT, ZIP_UNZIP_LIMIT, STOP_DUPLICATE, TORRENT_TIMEOUT, LOGGER, STORAGE_THRESHOLD, LEECH_LIMIT, \
                OWNER_ID, SUDO_USERS, PAID_USERS, PAID_SERVICE, QbInterval
from bot.helper.mirror_utils.status_utils.qbit_download_status import QbDownloadStatus
from bot.helper.mirror_utils.upload_utils.gdriveTools import GoogleDriveHelper
from bot.helper.telegram_helper.message_utils import sendMessage, sendMarkup, deleteMessage, sendStatusMessage, update_all_messages, sendFile
from bot.helper.ext_utils.bot_utils import get_readable_file_size, get_readable_time, setInterval, bt_selection_buttons, getDownloadByGid, new_thread
from bot.helper.ext_utils.fs_utils import clean_unwanted, get_base_name, check_storage_threshold
from bot.helper.telegram_helper import button_build

qb_download_lock = Lock()
STALLED_TIME = {}
STOP_DUP_CHECK = set()
RECHECKED = set()
UPLOADED = set()
SEEDING = set()

def __get_hash_magnet(mgt: str):
    hash_ = re_search(r'(?<=xt=urn:btih:)[a-zA-Z0-9]+', mgt).group(0)
    if len(hash_) == 32:
        hash_ = b16encode(b32decode(str(hash_))).decode()
    return str(hash_)

def __get_hash_file(path):
    with open(path, "rb") as f:
        decodedDict = bdecode(f.read())
        hash_ = sha1(bencode(decodedDict[b'info'])).hexdigest()
    return str(hash_)

def add_qb_torrent(link, path, listener, ratio, seed_time):
    client = get_client()
    ADD_TIME = time()
    try:
        if link.startswith('magnet:'):
            ext_hash = __get_hash_magnet(link)
        else:
            ext_hash = __get_hash_file(link)
        tor_info = client.torrents_info(torrent_hashes=ext_hash)
        if len(tor_info) > 0:
            sendMessage("This Torrent already added!", listener.bot, listener.message)
            return client.auth_log_out()
        if link.startswith('magnet:'):
            op = client.torrents_add(link, save_path=path, ratio_limit=ratio, seeding_time_limit=seed_time)
        else:
            op = client.torrents_add(torrent_files=[link], save_path=path, ratio_limit=ratio, seeding_time_limit=seed_time)
        sleep(0.3)
        if op.lower() == "ok.":
            tor_info = client.torrents_info(torrent_hashes=ext_hash)
            if len(tor_info) == 0:
                while True:
                    tor_info = client.torrents_info(torrent_hashes=ext_hash)
                    if len(tor_info) > 0:
                        break
                    elif time() - ADD_TIME >= 30:
                        msg = "Not a torrent. If it's a torrent then report!"
                        client.torrents_delete(torrent_hashes=ext_hash, delete_files=True)
                        sendMessage(msg, listener.bot, listener.message)
                        if not link.startswith('magnet:'):
                            remove(link)
                        return client.auth_log_out()
            if not link.startswith('magnet:'):
                remove(link)
        else:
            sendMessage("This is an unsupported/invalid link.", listener.bot, listener.message)
            if not link.startswith('magnet:'):
                remove(link)
            return client.auth_log_out()
        tor_info = tor_info[0]
        ext_hash = tor_info.hash
        with download_dict_lock:
            download_dict[listener.uid] = QbDownloadStatus(listener, ext_hash)
        with qb_download_lock:
            if not QbInterval:
                periodic = setInterval(3, __qb_listener)
                QbInterval.append(periodic)
        listener.onDownloadStart()
        LOGGER.info(f"QbitDownload started: {tor_info.name} - Hash: {ext_hash}")
        if BASE_URL is not None and listener.select:
            if link.startswith('magnet:'):
                metamsg = "Downloading Metadata, wait then you can select files. Use torrent file to avoid this wait."
                meta = sendMessage(metamsg, listener.bot, listener.message)
                while True:
                    tor_info = client.torrents_info(torrent_hashes=ext_hash)
                    if len(tor_info) == 0:
                        deleteMessage(listener.bot, meta)
                        return
                    try:
                        tor_info = tor_info[0]
                        if tor_info.state not in ["metaDL", "checkingResumeData", "pausedDL"]:
                            deleteMessage(listener.bot, meta)
                            break
                    except:
                        return deleteMessage(listener.bot, meta)
            client.torrents_pause(torrent_hashes=ext_hash)
            SBUTTONS = bt_selection_buttons(ext_hash)
            msg = "Your download paused. Choose files then press Done Selecting button to start downloading."
            sendMarkup(msg, listener.bot, listener.message, SBUTTONS)
        else:
            sendStatusMessage(listener.message, listener.bot)
    except Exception as e:
        sendMessage(str(e), listener.bot, listener.message)
        client.auth_log_out()


def __remove_torrent(client, hash_):
    with qb_download_lock:
        client.torrents_delete(torrent_hashes=hash_, delete_files=True)
        if hash_ in STALLED_TIME:
            del STALLED_TIME[hash_]
        if hash_ in STOP_DUP_CHECK:
            STOP_DUP_CHECK.remove(hash_)
        if hash_ in RECHECKED:
            RECHECKED.remove(hash_)
        if hash_ in UPLOADED:
            UPLOADED.remove(hash_)
        if hash_ in SEEDING:
            SEEDING.remove(hash_)

    # def __qb_listener(self):
    #     try:
    #         tor_info = self.client.torrents_info(torrent_hashes=self.ext_hash)
    #         if len(tor_info) == 0:
    #             return
    #         tor_info = tor_info[0]
    #         if tor_info.state == "metaDL":
    #             self.__stalled_time = time()
    #             if TORRENT_TIMEOUT is not None and time() - tor_info.added_on >= TORRENT_TIMEOUT:
    #                 self.__onDownloadError("Dead Torrent!")
    #         elif tor_info.state == "downloading":
    #             self.__stalled_time = time()
    #             if not self.__stopDup_check and not self.__listener.select and STOP_DUPLICATE and not self.__listener.isLeech:
    #                 LOGGER.info('Checking File/Folder if already in Drive')
    #                 qbname = tor_info.content_path.rsplit('/', 1)[-1].rsplit('.!qB', 1)[0]
    #                 if self.__listener.isZip:
    #                     qbname = f"{qbname}.zip"
    #                 elif self.__listener.extract:
    #                     try:
    #                         qbname = get_base_name(qbname)
    #                     except:
    #                         qbname = None
    #                 if qbname is not None:
    #                     if TELEGRAPH_STYLE is True:
    #                         qbmsg, button = GoogleDriveHelper().drive_list(qbname, True)
    #                         if qbmsg:
    #                             self.__onDownloadError("File/Folder is already available in Drive.")
    #                             sendMarkup("Here are the search results:", self.__listener.bot, self.__listener.message, button)
    #                     else:
    #                         cap, f_name = GoogleDriveHelper().drive_list(qbname, True)
    #                         if cap:
    #                             self.__onDownloadError("File/Folder is already available in Drive.")
    #                             cap = f"Here are the search results:\n\n{cap}"
    #                             sendFile(self.__listener.bot, self.__listener.message, f_name, cap)
    #                 self.__stopDup_check = True
    #             if not self.__sizeChecked:
    #                 size = tor_info.size
    #                 arch = any([self.__listener.isZip, self.__listener.extract])
    #                 user_id = self.__listener.message.from_user.id
    #                 if any([ZIP_UNZIP_LIMIT, LEECH_LIMIT, TORRENT_DIRECT_LIMIT, STORAGE_THRESHOLD]) and user_id != OWNER_ID and user_id not in SUDO_USERS and user_id not in PAID_USERS:
    #                     if PAID_SERVICE is True:
    #                         if STORAGE_THRESHOLD is not None:
    #                             acpt = check_storage_threshold(size, arch)
    #                             if not acpt:
    #                                 msg = f'You must leave {STORAGE_THRESHOLD}GB free storage.'
    #                                 msg += f'\nYour File/Folder size is {get_readable_file_size(size)}'
    #                                 msg += f'\n#Buy Paid Service'
    #                                 self.__onDownloadError(msg)
    #                                 return
    #                         limit = None
    #                         if ZIP_UNZIP_LIMIT is not None and arch:
    #                             mssg = f'Zip/Unzip limit is {ZIP_UNZIP_LIMIT}GB'
    #                             mssg += f'\n#Buy Paid Service'
    #                             limit = ZIP_UNZIP_LIMIT
    #                         if LEECH_LIMIT is not None and self.__listener.isLeech:
    #                             mssg = f'Leech limit is {LEECH_LIMIT}GB'
    #                             mssg += f'\n#Buy Paid Service'
    #                             limit = LEECH_LIMIT
    #                         elif TORRENT_DIRECT_LIMIT is not None:
    #                             mssg = f'Torrent limit is {TORRENT_DIRECT_LIMIT}GB'
    #                             mssg += f'\n#Buy Paid Service'
    #                             limit = TORRENT_DIRECT_LIMIT
    #                     else:
    #                         if STORAGE_THRESHOLD is not None:
    #                             acpt = check_storage_threshold(size, arch)
    #                             if not acpt:
    #                                 msg = f'You must leave {STORAGE_THRESHOLD}GB free storage.'
    #                                 msg += f'\nYour File/Folder size is {get_readable_file_size(size)}'
    #                                 self.__onDownloadError(msg)
    #                                 return
    #                         limit = None
    #                         if ZIP_UNZIP_LIMIT is not None and arch:
    #                             mssg = f'Zip/Unzip limit is {ZIP_UNZIP_LIMIT}GB'
    #                             limit = ZIP_UNZIP_LIMIT
    #                         if LEECH_LIMIT is not None and self.__listener.isLeech:
    #                             mssg = f'Leech limit is {LEECH_LIMIT}GB'
    #                             limit = LEECH_LIMIT
    #                         elif TORRENT_DIRECT_LIMIT is not None:
    #                             mssg = f'Torrent limit is {TORRENT_DIRECT_LIMIT}GB'
    #                             limit = TORRENT_DIRECT_LIMIT
    #                     if limit is not None:
    #                         LOGGER.info('Checking File/Folder Size...')
    #                         if size > limit * 1024**3:
    #                             fmsg = f"{mssg}.\nYour File/Folder size is {get_readable_file_size(size)}"
    #                             self.__onDownloadError(fmsg)
    #                 self.__sizeChecked = True
    #         elif tor_info.state == "stalledDL":
    #             if not self.__rechecked and 0.99989999999999999 < tor_info.progress < 1:
    #                 msg = f"Force recheck - Name: {self.__name} Hash: "
    #                 msg += f"{self.ext_hash} Downloaded Bytes: {tor_info.downloaded} "
    #                 msg += f"Size: {tor_info.size} Total Size: {tor_info.total_size}"
    #                 LOGGER.info(msg)
    #                 self.client.torrents_recheck(torrent_hashes=self.ext_hash)
    #                 self.__rechecked = True
    #             elif TORRENT_TIMEOUT is not None and time() - self.__stalled_time >= TORRENT_TIMEOUT:
    #                 self.__onDownloadError("Dead Torrent!")
    #         elif tor_info.state == "missingFiles":
    #             self.client.torrents_recheck(torrent_hashes=self.ext_hash)
    #         elif tor_info.state == "error":
    #             self.__onDownloadError("No enough space for this torrent on device")
    #         elif (tor_info.state.lower().endswith("up") or tor_info.state == "uploading") and not self.__uploaded:
    #             self.__uploaded = True
    #             if not self.__listener.seed:
    #                 self.client.torrents_pause(torrent_hashes=self.ext_hash)
    #             if self.__listener.select:
    #                 clean_unwanted(self.__path)
    #             self.__listener.onDownloadComplete()
    #             if self.__listener.seed:
    #                 with download_dict_lock:
    #                     if self.__listener.uid not in download_dict:
    #                         self.__remove_torrent()
    #                         return
    #                     download_dict[self.__listener.uid] = QbDownloadStatus(self.__listener, self)
    #                 self.is_seeding = True
    #                 update_all_messages()
    #                 LOGGER.info(f"Seeding started: {self.__name} - Hash: {self.ext_hash}")
    #             else:
    #                 self.__remove_torrent()
    #         elif tor_info.state == 'pausedUP' and self.__listener.seed:
    #             self.__listener.onUploadError(f"Seeding stopped with Ratio: {round(tor_info.ratio, 3)} and Time: {get_readable_time(tor_info.seeding_time)}")
    #             self.__remove_torrent()
    #         elif tor_info.state == 'pausedDL' and tor_info.completion_on != 0:
    #             # recheck torrent incase one of seed limits reached
    #             # sometimes it stuck on pausedDL from maxRatioAction but it should be pausedUP
    #             LOGGER.info("Recheck on complete manually! PausedDL")
    #             self.client.torrents_recheck(torrent_hashes=self.ext_hash)
    #     except Exception as e:
    #         LOGGER.error(str(e))

def __onDownloadError(err, client, tor):
    LOGGER.info(f"Cancelling Download: {tor.name}")
    client.torrents_pause(torrent_hashes=tor.hash)
    sleep(0.3)
    download = getDownloadByGid(tor.hash[:12])
    try:
        listener = download.listener()
        listener.onDownloadError(err)
    except:
        pass
    __remove_torrent(client, tor.hash)

@new_thread
def __onSeedFinish(client, tor):
    LOGGER.info(f"Cancelling Seed: {tor.name}")
    download = getDownloadByGid(tor.hash[:12])
    try:
        listener = download.listener()
        listener.onUploadError(f"Seeding stopped with Ratio: {round(tor.ratio, 3)} and Time: {get_readable_time(tor.seeding_time)}")
    except:
        pass
    __remove_torrent(client, tor.hash)

@new_thread
def __stop_duplicate(client, tor):
    download = getDownloadByGid(tor.hash[:12])
    try:
        listener = download.listener()
        if not listener.select and not listener.isLeech:
            LOGGER.info('Checking File/Folder if already in Drive')
            qbname = tor.content_path.rsplit('/', 1)[-1].rsplit('.!qB', 1)[0]
            if listener.isZip:
                qbname = f"{qbname}.zip"
            elif listener.extract:
                try:
                    qbname = get_base_name(qbname)
                except:
                    qbname = None
            if qbname is not None:

                if TELEGRAPH_STYLE is True:
                    qbmsg, button = GoogleDriveHelper().drive_list(qbname, True)
                    if qbmsg:
                        __onDownloadError("File/Folder is already available in Drive.")
                        sendMarkup("Here are the search results:", self.__listener.bot, self.__listener.message, button)
                else:
                    cap, f_name = GoogleDriveHelper().drive_list(qbname, True)
                    if cap:
                        __onDownloadError("File/Folder is already available in Drive.")
                        cap = f"Here are the search results:\n\n{cap}"
                        sendFile(self.__listener.bot, self.__listener.message, f_name, cap)

                cap, f_name = GoogleDriveHelper().drive_list(qbname, True)
                if cap:
                    __onDownloadError("File/Folder is already available in Drive.", client, tor)
                    cap = f"Here are the search results:\n\n{cap}"
                    sendFile(listener.bot, listener.message, f_name, cap)
                    return
    except:
        pass


@new_thread
def __onDownloadComplete(client, tor):
    download = getDownloadByGid(tor.hash[:12])
    try:
        listener = download.listener()
    except:
        return
    if not listener.seed:
        client.torrents_pause(torrent_hashes=tor.hash)
    if listener.select:
        clean_unwanted(tor.content_path.rsplit('/', 1)[0])
    listener.onDownloadComplete()
    if listener.seed:
        with download_dict_lock:
            if listener.uid not in download_dict:
                client.torrents_delete(torrent_hashes=tor.hash, delete_files=True)
                return
            download_dict[listener.uid] = QbDownloadStatus(listener, tor.hash, True)
        with qb_download_lock:
            SEEDING.add(tor.hash)
        update_all_messages()
        LOGGER.info(f"Seeding started: {tor.name} - Hash: {tor.hash}")
    else:
        __remove_torrent(client, tor.hash)

def __qb_listener():
    client = get_client()
    with qb_download_lock:
        if len(client.torrents_info()) == 0:
            QbInterval[0].cancel()
            QbInterval.clear()
            return
        try:
            for tor_info in client.torrents_info():
                if tor_info.state == "metaDL":
                    STALLED_TIME[tor_info.hash] = time()
                    if TORRENT_TIMEOUT is not None and time() - tor_info.added_on >= TORRENT_TIMEOUT:
                        Thread(target=__onDownloadError, args=("Dead Torrent!", client, tor_info)).start()
                elif tor_info.state == "downloading":
                    STALLED_TIME[tor_info.hash] = time()
                    if tor_info.hash not in STOP_DUP_CHECK and STOP_DUPLICATE:
                        STOP_DUP_CHECK.add(tor_info.hash)
                        __stop_duplicate(client, tor_info)
                elif tor_info.state == "stalledDL":
                    if tor_info.hash not in RECHECKED and 0.99989999999999999 < tor_info.progress < 1:
                        msg = f"Force recheck - Name: {tor_info.name} Hash: "
                        msg += f"{tor_info.hash} Downloaded Bytes: {tor_info.downloaded} "
                        msg += f"Size: {tor_info.size} Total Size: {tor_info.total_size}"
                        LOGGER.info(msg)
                        client.torrents_recheck(torrent_hashes=tor_info.hash)
                        RECHECKED.add(tor_info.hash)
                    elif TORRENT_TIMEOUT is not None and time() - STALLED_TIME[tor_info.hash] >= TORRENT_TIMEOUT:
                        Thread(target=__onDownloadError, args=("Dead Torrent!", client, tor_info)).start()
                elif tor_info.state == "missingFiles":
                    client.torrents_recheck(torrent_hashes=tor_info.hash)
                elif tor_info.state == "error":
                    Thread(target=__onDownloadError, args=("No enough space for this torrent on device", client, tor_info)).start()
                elif (tor_info.state.lower().endswith("up") or tor_info.state == "uploading") and tor_info.hash not in UPLOADED:
                    UPLOADED.add(tor_info.hash)
                    __onDownloadComplete(client, tor_info)
                elif tor_info.state == 'pausedUP' and tor_info.hash in SEEDING:
                    __onSeedFinish(client, tor_info)
                elif tor_info.state == 'pausedDL' and tor_info.completion_on != 0:
                    # recheck torrent incase one of seed limits reached
                    # sometimes it stuck on pausedDL from maxRatioAction but it should be pausedUP
                    if tor_info.hash not in RECHECKED:
                        LOGGER.info(f"Recheck on complete manually! PausedDL. Hash: {tor_info.hash}")
                        client.torrents_recheck(torrent_hashes=tor_info.hash)
                        RECHECKED.add(tor_info.hash)
        except Exception as e:
            LOGGER.error(str(e))