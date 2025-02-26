from base64 import b64encode
from requests import utils as rutils
from re import match as re_match, search as re_search, split as re_split
from time import sleep, time
from os import path as ospath, remove as osremove, listdir, walk
from shutil import rmtree
from threading import Thread
from subprocess import Popen
from html import escape
from telegram.ext import CommandHandler
from telegram import InlineKeyboardMarkup, ParseMode, InlineKeyboardButton

from bot import *
from bot.helper.ext_utils.bot_utils import is_url, is_magnet, is_gdtot_link, is_mega_link, is_gdrive_link, get_content_type, get_readable_time
from bot.helper.ext_utils.fs_utils import get_base_name, get_path_size, split_file, clean_download
from bot.helper.ext_utils.shortenurl import short_url
from bot.helper.ext_utils.exceptions import DirectDownloadLinkException, NotSupportedExtractionArchive
from bot.helper.mirror_utils.download_utils.aria2_download import add_aria2c_download
from bot.helper.mirror_utils.download_utils.gd_downloader import add_gd_download
from bot.helper.mirror_utils.download_utils.qbit_downloader import QbDownloader
from bot.helper.mirror_utils.download_utils.mega_downloader import MegaDownloader
from bot.helper.mirror_utils.download_utils.direct_link_generator import direct_link_generator
from bot.helper.mirror_utils.download_utils.telegram_downloader import TelegramDownloadHelper
from bot.helper.mirror_utils.status_utils.extract_status import ExtractStatus
from bot.helper.mirror_utils.status_utils.zip_status import ZipStatus
from bot.helper.mirror_utils.status_utils.split_status import SplitStatus
from bot.helper.mirror_utils.status_utils.upload_status import UploadStatus
from bot.helper.mirror_utils.status_utils.tg_upload_status import TgUploadStatus
from bot.helper.mirror_utils.upload_utils.gdriveTools import GoogleDriveHelper
from bot.helper.mirror_utils.upload_utils.pyrogramEngine import TgUploader
from bot.helper.telegram_helper.bot_commands import BotCommands
from bot.helper.telegram_helper.filters import CustomFilters
from bot.helper.telegram_helper.message_utils import sendMessage, sendMarkup, delete_all_messages, update_all_messages, auto_delete_upload_message
from bot.helper.telegram_helper.button_build import ButtonMaker
from bot.helper.ext_utils.db_handler import DbManger
from bot.helper.ext_utils.telegraph_helper import telegraph

class MirrorListener:
    def __init__(self, bot, message, isZip=False, extract=False, isQbit=False, isLeech=False, pswd=None, tag=None, select=False, seed=False):
        self.bot = bot
        self.message = message
        self.uid = self.message.message_id
        self.extract = extract
        self.isZip = isZip
        self.isQbit = isQbit
        self.isLeech = isLeech
        self.pswd = pswd
        self.tag = tag
        self.seed = any([seed, QB_SEED])
        self.select = select
        self.elapsed_time = time()
        self.isPrivate = self.message.chat.type in ['private', 'group']
        self.user_id = self.message.from_user.id	
        reply_to = self.message.reply_to_message
        self.suproc = None


    def clean(self):
        try:
            Interval[0].cancel()
            Interval.clear()
            aria2.purge()
            delete_all_messages()
        except:
            pass

    def onDownloadStart(self):
        if not self.isPrivate and INCOMPLETE_TASK_NOTIFIER and DB_URI is not None:
            DbManger().add_incomplete_task(self.message.chat.id, self.message.link, self.tag)

    def onDownloadComplete(self):
        with download_dict_lock:
            LOGGER.info(f"Download completed: {download_dict[self.uid].name()}")
            download = download_dict[self.uid]
            name = str(download.name()).replace('/', '')
            gid = download.gid()
            if name == "None" or self.isQbit or not ospath.exists(f'{DOWNLOAD_DIR}{self.uid}/{name}'):
                name = listdir(f'{DOWNLOAD_DIR}{self.uid}')[-1]
            m_path = f'{DOWNLOAD_DIR}{self.uid}/{name}'
        size = get_path_size(m_path)
        if self.isZip:
            path = m_path + ".zip"
            with download_dict_lock:
                download_dict[self.uid] = ZipStatus(name, size, gid, self)
            if self.pswd is not None:
                if self.isLeech and int(size) > MAX_LEECH_SIZE:
                    LOGGER.info(f'Zip: orig_path: {m_path}, zip_path: {path}.0*')
                    self.suproc = Popen(["7z", f"-v{MAX_LEECH_SIZE}b", "a", "-mx=0", f"-p{self.pswd}", path, m_path])
                else:
                    LOGGER.info(f'Zip: orig_path: {m_path}, zip_path: {path}')
                    self.suproc = Popen(["7z", "a", "-mx=0", f"-p{self.pswd}", path, m_path])
            elif self.isLeech and int(size) > MAX_LEECH_SIZE:
                LOGGER.info(f'Zip: orig_path: {m_path}, zip_path: {path}.0*')
                self.suproc = Popen(["7z", f"-v{MAX_LEECH_SIZE}b", "a", "-mx=0", path, m_path])
            else:
                LOGGER.info(f'Zip: orig_path: {m_path}, zip_path: {path}')
                self.suproc = Popen(["7z", "a", "-mx=0", path, m_path])
            self.suproc.wait()
            if self.suproc.returncode == -9:
                return
            elif self.suproc.returncode != 0:
                LOGGER.error('An error occurred while zipping! Uploading anyway')
                path = f'{DOWNLOAD_DIR}{self.uid}/{name}'
            if self.suproc.returncode == 0 and (not self.isQbit or not self.seed or self.isLeech):
                try:
                    rmtree(m_path)
                except:
                    osremove(m_path)
        elif self.extract:
            try:
                if ospath.isfile(m_path):
                    path = get_base_name(m_path)
                LOGGER.info(f"Extracting: {name}")
                with download_dict_lock:
                    download_dict[self.uid] = ExtractStatus(name, size, gid, self)
                if ospath.isdir(m_path):
                    for dirpath, subdir, files in walk(m_path, topdown=False):
                        for file_ in files:
                            if file_.endswith((".zip", ".7z")) or re_search(r'\.part0*1\.rar$|\.7z\.0*1$|\.zip\.0*1$', file_) \
                               or (file_.endswith(".rar") and not re_search(r'\.part\d+\.rar$', file_)):
                                m_path = ospath.join(dirpath, file_)
                                if self.pswd is not None:
                                    self.suproc = Popen(["7z", "x", f"-p{self.pswd}", m_path, f"-o{dirpath}", "-aot"])
                                else:
                                    self.suproc = Popen(["7z", "x", m_path, f"-o{dirpath}", "-aot"])
                                self.suproc.wait()
                                if self.suproc.returncode == -9:
                                    return
                                elif self.suproc.returncode != 0:
                                    LOGGER.error('Unable to extract archive splits! Uploading anyway')
                        if self.suproc is not None and self.suproc.returncode == 0:
                            for file_ in files:
                                if file_.endswith((".rar", ".zip", ".7z")) or \
                                    re_search(r'\.r\d+$|\.7z\.\d+$|\.z\d+$|\.zip\.\d+$', file_):
                                    del_path = ospath.join(dirpath, file_)
                                    osremove(del_path)
                    path = f'{DOWNLOAD_DIR}{self.uid}/{name}'
                else:
                    if self.pswd is not None:
                        self.suproc = Popen(["bash", "pextract", m_path, self.pswd])
                    else:
                        self.suproc = Popen(["bash", "extract", m_path])
                    self.suproc.wait()
                    if self.suproc.returncode == -9:
                        return
                    elif self.suproc.returncode == 0:
                        LOGGER.info(f"Extracted Path: {path}")
                        osremove(m_path)
                    else:
                        LOGGER.error('Unable to extract archive! Uploading anyway')
                        path = f'{DOWNLOAD_DIR}{self.uid}/{name}'
            except NotSupportedExtractionArchive:
                LOGGER.info("Not any valid archive, uploading file as it is.")
                path = f'{DOWNLOAD_DIR}{self.uid}/{name}'
        else:
            path = f'{DOWNLOAD_DIR}{self.uid}/{name}'
        up_name = path.rsplit('/', 1)[-1]
        if self.isLeech and not self.isZip:
            checked = False
            for dirpath, subdir, files in walk(f'{DOWNLOAD_DIR}{self.uid}', topdown=False):
                for file_ in files:
                    f_path = ospath.join(dirpath, file_)
                    f_size = ospath.getsize(f_path)
                    if int(f_size) > MAX_LEECH_SIZE:
                        if not checked:
                            checked = True
                            with download_dict_lock:
                                download_dict[self.uid] = SplitStatus(up_name, size, gid, self)
                            LOGGER.info(f"Splitting: {up_name}")
                        res = split_file(f_path, f_size, file_, dirpath, MAX_LEECH_SIZE, self)
                        if not res:
                            return
                        osremove(f_path)
        if self.isLeech:
            size = get_path_size(f'{DOWNLOAD_DIR}{self.uid}')
            LOGGER.info(f"Leech Name: {up_name}")
            tg = TgUploader(up_name, self)
            tg_upload_status = TgUploadStatus(tg, size, gid, self)
            with download_dict_lock:
                download_dict[self.uid] = tg_upload_status
            update_all_messages()
            tg.upload()
        else:
            up_path = f'{DOWNLOAD_DIR}{self.uid}/{up_name}'
            size = get_path_size(up_path)
            LOGGER.info(f"Upload Name: {up_name}")
            drive = GoogleDriveHelper(up_name, self)
            upload_status = UploadStatus(drive, size, gid, self)
            with download_dict_lock:
                download_dict[self.uid] = upload_status
            update_all_messages()
            drive.upload(up_name)

    def onDownloadError(self, error):
        error = error.replace('<', ' ').replace('>', ' ')
        clean_download(f'{DOWNLOAD_DIR}{self.uid}')
        with download_dict_lock:
            try:
                del download_dict[self.uid]
            except Exception as e:
                LOGGER.error(str(e))
            count = len(download_dict)
        msg = f"⚠⁉ {self.tag}\n<b>Download has been stopped</b>\n<b>Due to: </b>{error}\n<b>Elapsed : </b>{get_readable_time(time() - self.message.date.timestamp())}"
        sendMessage(msg, self.bot, self.message)
        if count == 0:
            self.clean()
        else:
            update_all_messages()

        if not self.isPrivate and INCOMPLETE_TASK_NOTIFIER and DB_URI is not None:
            DbManger().rm_complete_task(self.message.link)

    def onUploadComplete(self, link: str, size, files, folders, typ, name: str):
        buttons = ButtonMaker()
        mesg = self.message.text.split('\n')
        message_args = mesg[0].split(' ', maxsplit=1)
        reply_to = self.message.reply_to_message
        slmsg = f"Added by: {self.tag} \n👥 User ID: <code>{self.user_id}</code>\n\n"
        if LINK_LOGS:
            try:
                source_link = message_args[1]
                for link_log in LINK_LOGS:
                    bot.sendMessage(link_log, text=slmsg + source_link, parse_mode=ParseMode.HTML )
            except IndexError:
                pass
            if reply_to is not None:
                try:
                    reply_text = reply_to.text
                    if is_url(reply_text):
                        source_link = reply_text.strip()
                        for link_log in LINK_LOGS:
                            bot.sendMessage(chat_id=link_log, text=slmsg + source_link, parse_mode=ParseMode.HTML )
                except TypeError:
                    pass
        if AUTO_DELETE_UPLOAD_MESSAGE_DURATION != -1:
            reply_to = self.message.reply_to_message
            if reply_to is not None:
                reply_to.delete()
            auto_delete_message = int(AUTO_DELETE_UPLOAD_MESSAGE_DURATION / 60)
            if self.message.chat.type == 'private':
                warnmsg = ''
            else:
                warnmsg = f'<b>This message will be deleted in <i>{auto_delete_message} minutes</i> from this group.</b>\n'
        else:
            warnmsg = ''
        if BOT_PM and self.message.chat.type != 'private':
            pmwarn = f"<b>I have sent files in PM.</b>\n"
        elif self.message.chat.type == 'private':
            pmwarn = ''
        else:
            pmwarn = ''
        if MIRROR_LOGS and self.message.chat.type != 'private':
            logwarn = f"<b>I have sent files in Mirror Log Channel.(Join Mirror Log channel) </b>\n"
        elif self.message.chat.type == 'private':
            logwarn = ''
        else:
            logwarn = ''
        if LEECH_LOG and self.message.chat.type != 'private':
            logleechwarn = f"<b>I have sent files in Leech Log Channel.(Join Leech Log channel) </b>\n"
        elif self.message.chat.type == 'private':
            logleechwarn = ''
        else:
            logleechwarn = ''
        if not self.isPrivate and INCOMPLETE_TASK_NOTIFIER and DB_URI is not None:
            DbManger().rm_complete_task(self.message.link)
        msg = f"<b>╭Name: </b><code>{escape(name)}</code>\n<b>├Size: </b>{size}"
        if self.isLeech:
            if SOURCE_LINK is True:
                try:
                    source_link = message_args[1]
                    if is_magnet(source_link):
                        link = telegraph.create_page(
                        title='ReflectionMirror Source Link',
                        content=source_link,
                    )["path"]
                        buttons.buildbutton(f" Source Link", f"https://telegra.ph/{link}")
                    else:
                        buttons.buildbutton(f" Source Link", source_link)
                except Exception:
                    pass
                if reply_to is not None:
                    try:
                        reply_text = reply_to.text
                        if is_url(reply_text):
                            source_link = reply_text.strip()
                            if is_magnet(source_link):
                                link = telegraph.create_page(
                                    title='WeebZone Source Link',
                                    content=source_link,
                                )["path"]
                                buttons.buildbutton(f" Source Link", f"https://telegra.ph/{link}")
                            else:
                                buttons.buildbutton(f" Source Link", source_link)
                    except Exception:
                        pass
            msg += f'\n<b>Total Files: </b>{folders}'
            if typ != 0:
                msg += f'\n<b>Corrupted Files: </b>{typ}'
            msg += f'\n<b>It Tooks:</b> {get_readable_time(time() - self.message.date.timestamp())}'
            msg += f'\n<b>cc: </b>{self.tag}'
            msg += f'\n<b>Thanks For using {TITLE_NAME}</b>\n'
            if LEECH_LOG:
                for i in LEECH_LOG:
                    indexmsg = ''
                    for index, (link, name) in enumerate(files.items(), start=1):
                        indexmsg += f"{index}. <a href='{link}'>{name}</a>\n"
                        if len(indexmsg.encode() + msg.encode()) > 4000:
                            sleep(1.5)
                            bot.sendMessage(chat_id=i, text=msg + indexmsg,
                                            reply_markup=InlineKeyboardMarkup(buttons.build_menu(1)),
                                            parse_mode=ParseMode.HTML)
                            indexmsg = ''
                    if indexmsg != '':
                        sleep(1.5)
                        bot.sendMessage(chat_id=i, text=msg + indexmsg,
                                        reply_markup=InlineKeyboardMarkup(buttons.build_menu(1)),
                                        parse_mode=ParseMode.HTML)

            if BOT_PM:	
                bot_d = bot.get_me()	
                b_uname = bot_d.username	
                botstart = f"http://t.me/{b_uname}"	
                buttons.buildbutton("View file in PM", f"{botstart}")
            if not files:
                uploadmsg = sendMessage(msg, self.bot, self.message)
            else:
                fmsg = ''
                for index, (link, name) in enumerate(files.items(), start=1):
                    fmsg += f"{index}. <a href='{link}'>{name}</a>\n\n"
                    if len(fmsg.encode() + msg.encode()) > 4000:
                        uploadmsg = sendMarkup(msg + fmsg + pmwarn + logleechwarn + warnmsg, self.bot, self.message, InlineKeyboardMarkup(buttons.build_menu(2)))
                        sleep(1)
                        fmsg = ''
                if fmsg != '':
                    uploadmsg = sendMarkup(msg + fmsg + pmwarn + logleechwarn + warnmsg, self.bot, self.message, InlineKeyboardMarkup(buttons.build_menu(2)))
                    Thread(target=auto_delete_upload_message, args=(bot, self.message, uploadmsg)).start()
        else:
            msg += f'\n<b>Type: </b>{typ}'
            if ospath.isdir(f'{DOWNLOAD_DIR}{self.uid}/{name}'):
                msg += f'\n<b>SubFolders: </b>{folders}'
                msg += f'\n<b>Files: </b>{files}'
            msg += f'\n<b>It Tooks:</b> {get_readable_time(time() - self.message.date.timestamp())}'
            msg += f'\n<b>cc: </b>{self.tag}'
            msg += f'\n<b>Thanks For using {TITLE_NAME}</b>\n'
            buttons = ButtonMaker()
            link = short_url(link)
            buttons.buildbutton("Drive Link", link)
            LOGGER.info(f'Done Uploading {name}')
            if INDEX_URL is not None:
                url_path = rutils.quote(f'{name}')
                share_url = f'{INDEX_URL}/{url_path}'
                if ospath.isdir(f'{DOWNLOAD_DIR}/{self.uid}/{name}'):
                    share_url += '/'
                    share_url = short_url(share_url)
                    buttons.buildbutton("Index Link", share_url)
                else:
                    share_url = short_url(share_url)
                    buttons.buildbutton(" Index Link", share_url)
                    if VIEW_LINK:
                        share_urls = f'{INDEX_URL}/{url_path}?a=view'
                        share_urls = short_url(share_urls)
                        buttons.buildbutton("View Link", share_urls)
                    if BOT_PM:	
                        bot_d = bot.get_me()	
                        b_uname = bot_d.username	
                        botstart = f"http://t.me/{b_uname}"	
                        buttons.buildbutton("View file in PM", f"{botstart}")
            if BUTTON_FOUR_NAME is not None and BUTTON_FOUR_URL is not None:
                buttons.buildbutton(f"{BUTTON_FOUR_NAME}", f"{BUTTON_FOUR_URL}")
            if BUTTON_FIVE_NAME is not None and BUTTON_FIVE_URL is not None:
                buttons.buildbutton(f"{BUTTON_FIVE_NAME}", f"{BUTTON_FIVE_URL}")
            if BUTTON_SIX_NAME is not None and BUTTON_SIX_URL is not None:
                buttons.buildbutton(f"{BUTTON_SIX_NAME}", f"{BUTTON_SIX_URL}")
            if SOURCE_LINK is True:
                try:
                    mesg = message_args[1]
                    if is_magnet(mesg):
                        link = telegraph.create_page(
                            title='ReflectionMirror Source Link',
                            content=mesg,
                        )["path"]
                        buttons.buildbutton(f" Source Link", f"https://telegra.ph/{link}")
                    elif is_url(mesg):
                        source_link = mesg
                        if source_link.startswith(("|", "pswd: ")):
                            pass
                        else:
                            buttons.buildbutton(f"🔗 Source Link", source_link)
                    else:
                        pass
                except Exception:
                    pass
            if reply_to is not None:
                try:
                    reply_text = reply_to.text
                    if is_url(reply_text):
                        source_link = reply_text.strip()
                        if is_magnet(source_link):
                            link = telegraph.create_page(
                                title='WeebZone Source Link',
                                content=source_link,
                            )["path"]
                            buttons.buildbutton(f"Source Link", f"https://telegra.ph/{link}")
                        else:
                            buttons.buildbutton(f"Source Link", source_link)
                except Exception:
                    pass
            else:
                pass
            uploadmsg = sendMarkup(msg + pmwarn + logwarn + warnmsg, self.bot, self.message, InlineKeyboardMarkup(buttons.build_menu(2)))
            Thread(target=auto_delete_upload_message, args=(bot, self.message, uploadmsg)).start()
            if MIRROR_LOGS:	
                try:	
                    for chatid in MIRROR_LOGS:	
                        bot.sendMessage(chat_id=chatid, text=msg,	
                                        reply_markup=InlineKeyboardMarkup(buttons.build_menu(2)),	
                                        parse_mode=ParseMode.HTML)	
                except Exception as e:	
                    LOGGER.warning(e)	
            if BOT_PM and self.message.chat.type != 'private':	
                try:	
                    bot.sendMessage(chat_id=self.user_id, text=msg,	
                                    reply_markup=InlineKeyboardMarkup(buttons.build_menu(2)),	
                                    parse_mode=ParseMode.HTML)	
                except Exception as e:	
                    LOGGER.warning(e)	
                    return
            if self.isQbit and self.seed and not self.extract:
                if self.isZip:
                    try:
                        osremove(f'{DOWNLOAD_DIR}{self.uid}/{name}')
                    except:
                        pass
                return
        clean_download(f'{DOWNLOAD_DIR}{self.uid}')
        with download_dict_lock:
            try:
                del download_dict[self.uid]
            except Exception as e:
                LOGGER.error(str(e))
            count = len(download_dict)
        if count == 0:
            self.clean()
        else:
            update_all_messages()

    def onUploadError(self, error):
        e_str = error.replace('<', '').replace('>', '')
        clean_download(f'{DOWNLOAD_DIR}{self.uid}')
        with download_dict_lock:
            try:
                del download_dict[self.uid]
            except Exception as e:
                LOGGER.error(str(e))
            count = len(download_dict)
        sendMessage(f"{self.tag} {e_str}", self.bot, self.message)
        if count == 0:
            self.clean()
        else:
            update_all_messages()

        if not self.isPrivate and INCOMPLETE_TASK_NOTIFIER and DB_URI is not None:
            DbManger().rm_complete_task(self.message.link)

def _mirror(bot, message, isZip=False, extract=False, isQbit=False, isLeech=False, pswd=None, multi=0, select=False, seed=False):
    buttons = ButtonMaker()	
    if FSUB:
        try:
            uname = message.from_user.mention_html(
                message.from_user.first_name)
            user = bot.get_chat_member(FSUB_CHANNEL_ID, message.from_user.id)
            if user.status not in ['member', 'creator', 'administrator']:
                buttons.buildbutton(
                    f"{CHANNEL_USERNAME}",
                    f"https://t.me/{CHANNEL_USERNAME}")
                reply_markup = InlineKeyboardMarkup(buttons.build_menu(1))
                return sendMarkup(
                    f"<b>Dear {uname}️,\n\nI found that you haven't joined our Updates Channel yet.\n\nJoin and Use Bots Without Restrictions.</b>",
                    bot,
                    message,
                    reply_markup)
        except Exception as e:
            LOGGER.info(str(e))
    if BOT_PM and message.chat.type != 'private':
        try:
            msg1 = f'Added your Requested link to Download\n'
            send = bot.sendMessage(message.from_user.id, text=msg1)
            send.delete()
        except Exception as e:
            LOGGER.warning(e)
            bot_d = bot.get_me()
            b_uname = bot_d.username
            uname = f'<a href="tg://user?id={message.from_user.id}">{message.from_user.first_name}</a>'
            botstart = f"http://t.me/{b_uname}"
            buttons.buildbutton("Click Here to Start Me", f"{botstart}")
            startwarn = f"Dear {uname},\n\n<b>I found that you haven't started me in PM (Private Chat) yet.</b>\n\n" \
                        f"From now on i will give link and leeched files in PM and log channel only"
            message = sendMarkup(startwarn, bot, message, InlineKeyboardMarkup(buttons.build_menu(2)))
            return
    if message.chat.type == 'private' and len(LEECH_LOG) == 0 and isLeech and MAX_LEECH_SIZE == 4194304000:
        text = f"Leech Log is Empty you Can't use bot in PM,\nYou Can use <i>/{BotCommands.AddleechlogCommand} chat_id </i> to add leech log."
        sendMessage(text, bot, message)
        return
    mesg = message.text.split('\n')
    message_args = mesg[0].split(maxsplit=1)
    name_args = mesg[0].split('|', maxsplit=1)
    is_gdtot = False
    index = 1
    
    if len(message_args) > 1:
        link = message_args[1].strip()
        args = mesg[0].split(maxsplit=3)
        if "s" in [x.strip() for x in args]:
            select = True
            index += 1
        if "d" in [x.strip() for x in args]:
            seed = True
            index += 1
        message_args = mesg[0].split(maxsplit=index)
        if len(message_args) > index:
            link = message_args[index].strip()
            if link.isdigit():
                multi = int(link)
                link = ''
            elif link.startswith(("|", "pswd:")):
                link = ''
        else:
            link = ''
    else:
        link = ''

    if len(name_args) > 1:
        name = name_args[1]
        name = name.split(' pswd:')[0]
        name = name.strip()
    else:
        name = ''
    
    link = re_split(r"pswd:|\|", link)[0]
    link = link.strip()

    pswd_arg = mesg[0].split(' pswd: ')
    if len(pswd_arg) > 1:
        pswd = pswd_arg[1]


    if message.from_user.username:
        tag = f"@{message.from_user.username}"
    else:
        tag = message.from_user.mention_html(message.from_user.first_name)

    reply_to = message.reply_to_message
    if reply_to is not None:
        file = None
        media_array = [reply_to.document, reply_to.video, reply_to.audio]
        for i in media_array:
            if i is not None:
                file = i
                break

        if not reply_to.from_user.is_bot:
            if reply_to.from_user.username:
                tag = f"@{reply_to.from_user.username}"
            else:
                tag = reply_to.from_user.mention_html(reply_to.from_user.first_name)

        if (
            not is_url(link)
            and not is_magnet(link)
            or len(link) == 0
        ):

            if file is None:
                reply_text = reply_to.text.split(maxsplit=1)[0].strip()
                if is_url(reply_text) or is_magnet(reply_text):
                    link = reply_text
            elif file.mime_type != "application/x-bittorrent" and not isQbit:
                listener = MirrorListener(bot, message, isZip, extract, isQbit, isLeech, pswd, tag)
                Thread(target=TelegramDownloadHelper(listener).add_download, args=(message, f'{DOWNLOAD_DIR}{listener.uid}/', name)).start()
                if multi > 1:
                    sleep(4)
                    nextmsg = type('nextmsg', (object, ), {'chat_id': message.chat_id, 'message_id': message.reply_to_message.message_id + 1})
                    nextmsg = sendMessage(message_args[0], bot, nextmsg)
                    nextmsg.from_user.id = message.from_user.id
                    multi -= 1
                    sleep(4)
                    Thread(target=_mirror, args=(bot, nextmsg, isZip, extract, isQbit, isLeech, pswd, multi)).start()
                return
            else:
                link = file.get_file().file_path

    if not is_url(link) and not is_magnet(link) and not ospath.exists(link):
        help_msg = "<b>Send link along with command line:</b>"
        help_msg += "\n<code>/command</code> {link} |newname pswd: xx [zip/unzip]"
        help_msg += "\n\n<b>By replying to link or file:</b>"
        help_msg += "\n<code>/command</code> |newname pswd: xx [zip/unzip]"
        help_msg += "\n\n<b>Direct link authorization:</b>"
        help_msg += "\n<code>/command</code> {link} |newname pswd: xx\nusername\npassword"
        help_msg += "\n\n<b>Qbittorrent selection and seed:</b>"
        help_msg += "\n<code>/qbcommand</code> <b>s</b>(for selection) <b>d</b>(for seeding) {link} or by replying to {file/link}"
        help_msg += "\n\n<b>Multi links only by replying to first link or file:</b>"
        help_msg += "\n<code>/command</code> 10(number of links/files)\n\n<b>⚠⁉ If You Don't Know How To Use Bots, Check Others Message. Don't Play With Commands</b>"
        return sendMessage(help_msg, bot, message)

    LOGGER.info(link)

    if not is_mega_link(link) and not isQbit and not is_magnet(link) \
        and not is_gdrive_link(link) and not link.endswith('.torrent'):
        content_type = get_content_type(link)
        if content_type is None or re_match(r'text/html|text/plain', content_type):
            try:
                is_gdtot = is_gdtot_link(link)
                link = direct_link_generator(link)
                LOGGER.info(f"Generated link: {link}")
            except DirectDownloadLinkException as e:
                LOGGER.info(str(e))
                if str(e).startswith('ERROR:'):
                    return sendMessage(str(e), bot, message)

    listener = MirrorListener(bot, message, isZip, extract, isQbit, isLeech, pswd, tag, select, seed)

    if is_gdrive_link(link):
        if not isZip and not extract and not isLeech:
            gmsg = f"Use /{BotCommands.CloneCommand} to clone Google Drive file/folder\n\n"
            gmsg += f"Use /{BotCommands.ZipMirrorCommand} to make zip of Google Drive folder\n\n"
            gmsg += f"Use /{BotCommands.UnzipMirrorCommand} to extracts Google Drive archive folder/file"
            sendMessage(gmsg, bot, message)
        else:
            Thread(target=add_gd_download, args=(link, listener, is_gdtot, name)).start()
    elif is_mega_link(link):
        if MEGA_KEY is not None:
            Thread(target=MegaDownloader(listener).add_download, args=(link, f'{DOWNLOAD_DIR}{listener.uid}/')).start()
        else:
            sendMessage('MEGA_API_KEY not Provided!', bot, message)
    elif isQbit:
        Thread(target=QbDownloader(listener).add_qb_torrent, args=(link, f'{DOWNLOAD_DIR}{listener.uid}', select)).start()
    else:
        if len(mesg) > 1:
            try:
                ussr = mesg[1]
            except:
                ussr = ''
            try:
                pssw = mesg[2]
            except:
                pssw = ''
            auth = f"{ussr}:{pssw}"
            auth = "Basic " + b64encode(auth.encode()).decode('ascii')
        else:
            auth = ''
        Thread(target=add_aria2c_download, args=(link, f'{DOWNLOAD_DIR}{listener.uid}', listener, name, auth, select)).start()

    if multi > 1:
        sleep(4)
        nextmsg = type('nextmsg', (object, ), {'chat_id': message.chat_id, 'message_id': message.reply_to_message.message_id + 1})
        msg = message_args[0]
        if len(mesg) > 2:
            msg += '\n' + mesg[1] + '\n' + mesg[2]
        nextmsg = sendMessage(msg, bot, nextmsg)
        nextmsg.from_user.id = message.from_user.id
        multi -= 1
        sleep(4)
        Thread(target=_mirror, args=(bot, nextmsg, isZip, extract, isQbit, isLeech, pswd, multi, select, seed)).start()


def mirror(update, context):
    _mirror(context.bot, update.message)

def unzip_mirror(update, context):
    _mirror(context.bot, update.message, extract=True)

def zip_mirror(update, context):
    _mirror(context.bot, update.message, True)

def qb_mirror(update, context):
    _mirror(context.bot, update.message, isQbit=True)

def qb_unzip_mirror(update, context):
    _mirror(context.bot, update.message, extract=True, isQbit=True)

def qb_zip_mirror(update, context):
    _mirror(context.bot, update.message, True, isQbit=True)

def leech(update, context):
    _mirror(context.bot, update.message, isLeech=True)

def unzip_leech(update, context):
    _mirror(context.bot, update.message, extract=True, isLeech=True)

def zip_leech(update, context):
    _mirror(context.bot, update.message, True, isLeech=True)

def qb_leech(update, context):
    _mirror(context.bot, update.message, isQbit=True, isLeech=True)

def qb_unzip_leech(update, context):
    _mirror(context.bot, update.message, extract=True, isQbit=True, isLeech=True)

def qb_zip_leech(update, context):
    _mirror(context.bot, update.message, True, isQbit=True, isLeech=True)

if MIRROR_ENABLED:

    mirror_handler = CommandHandler(BotCommands.MirrorCommand, mirror,
                                    filters=CustomFilters.authorized_chat | CustomFilters.authorized_user, run_async=True)
    unzip_mirror_handler = CommandHandler(BotCommands.UnzipMirrorCommand, unzip_mirror,
                                    filters=CustomFilters.authorized_chat | CustomFilters.authorized_user, run_async=True)
    zip_mirror_handler = CommandHandler(BotCommands.ZipMirrorCommand, zip_mirror,
                                    filters=CustomFilters.authorized_chat | CustomFilters.authorized_user, run_async=True)
    qb_mirror_handler = CommandHandler(BotCommands.QbMirrorCommand, qb_mirror,
                                    filters=CustomFilters.authorized_chat | CustomFilters.authorized_user, run_async=True)
    qb_unzip_mirror_handler = CommandHandler(BotCommands.QbUnzipMirrorCommand, qb_unzip_mirror,
                                    filters=CustomFilters.authorized_chat | CustomFilters.authorized_user, run_async=True)
    qb_zip_mirror_handler = CommandHandler(BotCommands.QbZipMirrorCommand, qb_zip_mirror,
                                    filters=CustomFilters.authorized_chat | CustomFilters.authorized_user, run_async=True)

else:
    mirror_handler = CommandHandler(BotCommands.MirrorCommand, mirror,
                                    filters=CustomFilters.owner_filter | CustomFilters.authorized_user, run_async=True)
    unzip_mirror_handler = CommandHandler(BotCommands.UnzipMirrorCommand, unzip_mirror,
                                    filters=CustomFilters.owner_filter | CustomFilters.authorized_user, run_async=True)
    zip_mirror_handler = CommandHandler(BotCommands.ZipMirrorCommand, zip_mirror,
                                    filters=CustomFilters.owner_filter | CustomFilters.authorized_user, run_async=True)
    qb_mirror_handler = CommandHandler(BotCommands.QbMirrorCommand, qb_mirror,
                                    filters=CustomFilters.owner_filter | CustomFilters.authorized_user, run_async=True)
    qb_unzip_mirror_handler = CommandHandler(BotCommands.QbUnzipMirrorCommand, qb_unzip_mirror,
                                    filters=CustomFilters.owner_filter | CustomFilters.authorized_user, run_async=True)
    qb_zip_mirror_handler = CommandHandler(BotCommands.QbZipMirrorCommand, qb_zip_mirror,
                                    filters=CustomFilters.owner_filter | CustomFilters.authorized_user, run_async=True)

if LEECH_ENABLED:
    leech_handler = CommandHandler(BotCommands.LeechCommand, leech,
                                    filters=CustomFilters.authorized_chat | CustomFilters.authorized_user, run_async=True)
    unzip_leech_handler = CommandHandler(BotCommands.UnzipLeechCommand, unzip_leech,
                                    filters=CustomFilters.authorized_chat | CustomFilters.authorized_user, run_async=True)
    zip_leech_handler = CommandHandler(BotCommands.ZipLeechCommand, zip_leech,
                                    filters=CustomFilters.authorized_chat | CustomFilters.authorized_user, run_async=True)
    qb_leech_handler = CommandHandler(BotCommands.QbLeechCommand, qb_leech,
                                    filters=CustomFilters.authorized_chat | CustomFilters.authorized_user, run_async=True)
    qb_unzip_leech_handler = CommandHandler(BotCommands.QbUnzipLeechCommand, qb_unzip_leech,
                                    filters=CustomFilters.authorized_chat | CustomFilters.authorized_user, run_async=True)
    qb_zip_leech_handler = CommandHandler(BotCommands.QbZipLeechCommand, qb_zip_leech,
                                    filters=CustomFilters.authorized_chat | CustomFilters.authorized_user, run_async=True)

else:
    leech_handler = CommandHandler(BotCommands.LeechCommand, leech,
                                    filters=CustomFilters.owner_filter | CustomFilters.authorized_user, run_async=True)
    unzip_leech_handler = CommandHandler(BotCommands.UnzipLeechCommand, unzip_leech,
                                    filters=CustomFilters.owner_filter | CustomFilters.authorized_user, run_async=True)
    zip_leech_handler = CommandHandler(BotCommands.ZipLeechCommand, zip_leech,
                                    filters=CustomFilters.owner_filter | CustomFilters.authorized_user, run_async=True)
    qb_leech_handler = CommandHandler(BotCommands.QbLeechCommand, qb_leech,
                                    filters=CustomFilters.owner_filter | CustomFilters.authorized_user, run_async=True)
    qb_unzip_leech_handler = CommandHandler(BotCommands.QbUnzipLeechCommand, qb_unzip_leech,
                                    filters=CustomFilters.owner_filter | CustomFilters.authorized_user, run_async=True)
    qb_zip_leech_handler = CommandHandler(BotCommands.QbZipLeechCommand, qb_zip_leech,
                                    filters=CustomFilters.owner_filter | CustomFilters.authorized_user, run_async=True)

dispatcher.add_handler(mirror_handler)
dispatcher.add_handler(unzip_mirror_handler)
dispatcher.add_handler(zip_mirror_handler)
dispatcher.add_handler(qb_mirror_handler)
dispatcher.add_handler(qb_unzip_mirror_handler)
dispatcher.add_handler(qb_zip_mirror_handler)
dispatcher.add_handler(leech_handler)
dispatcher.add_handler(unzip_leech_handler)
dispatcher.add_handler(zip_leech_handler)
dispatcher.add_handler(qb_leech_handler)
dispatcher.add_handler(qb_unzip_leech_handler)
dispatcher.add_handler(qb_zip_leech_handler)
