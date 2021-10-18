import pickle
import sqlite3
from pathlib import Path
from typing import Generator, List, Union

import cv2
import numpy as np

from .FMask import FMask
from .UFaceMark import UFaceMark
from .UImage import UImage
from .UPerson import UPerson


class Faceset:

    def __init__(self, path):
        """
        Faceset is a class to store and manage face related data.

        arguments:

            path       path to faceset .dfs file
        """

        self._path = path = Path(path)

        if path.suffix != '.dfs':
            raise ValueError('Path must be a .dfs file')

        self._conn = conn = sqlite3.connect(path, isolation_level=None)
        self._cur = cur = conn.cursor()

        cur.execute('BEGIN IMMEDIATE')
        if not self._is_table_exists('FacesetInfo'):
            self.clear_db(transaction=False)
        cur.execute('COMMIT')

    def close(self):
        self._cur.close()
        self._cur = None
        self._conn.close()
        self._conn = None

    def _is_table_exists(self, name):
        return self._cur.execute(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", [name]).fetchone()[0] != 0

    def shrink(self):
        self._cur.execute('VACUUM')
        
    def clear_db(self, transaction=True):
        """
        delete all data and recreate DB
        """
        cur = self._cur

        if transaction:
            cur.execute('BEGIN IMMEDIATE')

        for table_name, in cur.execute("SELECT name from sqlite_master where type = 'table';").fetchall():
            cur.execute(f'DROP TABLE {table_name}')

        (cur.execute('CREATE TABLE FacesetInfo (version INT)')
            .execute('INSERT INTO  FacesetInfo VALUES (1)')

            .execute('CREATE TABLE UImage (uuid BLOB, name TEXT, format TEXT, data BLOB)')
            .execute('CREATE TABLE UPerson (uuid BLOB, name TEXT, age NUMERIC)')
            .execute('CREATE TABLE UFaceMark (uuid BLOB, UImage_uuid BLOB, UPerson_uuid BLOB, pickled_bytes BLOB)')
            )

        if transaction:
            cur.execute('COMMIT')
        
    ###################
    ### UFaceMark
    ###################
    def _UFaceMark_from_db_row(self, db_row) -> UFaceMark:
        uuid, UImage_uuid, UPerson_uuid, pickled_bytes = db_row
        return pickle.loads(pickled_bytes)
    
    def add_UFaceMark(self, fm : UFaceMark):
        """
        add or update UFaceMark in DB
        """
        pickled_bytes = pickle.dumps(fm)
        uuid = fm.get_uuid()
        UImage_uuid = fm.get_UImage_uuid()
        UPerson_uuid = fm.get_UPerson_uuid()

        cur = self._cur
        cur.execute('BEGIN IMMEDIATE')
        if cur.execute('SELECT COUNT(*) from UFaceMark where uuid=?', [uuid] ).fetchone()[0] != 0:
            cur.execute('UPDATE UFaceMark SET UImage_uuid=?, UPerson_uuid=?, pickled_bytes=? WHERE uuid=?',
                        [UImage_uuid, UPerson_uuid, pickled_bytes, uuid])
        else:
            cur.execute('INSERT INTO UFaceMark VALUES (?, ?, ?, ?)', [uuid, UImage_uuid, UPerson_uuid, pickled_bytes])
        cur.execute('COMMIT')

    def get_UFaceMark_count(self) -> int:
        return self._cur.execute('SELECT COUNT(*) FROM UFaceMark').fetchone()[0]

    def get_all_UFaceMark(self) -> List[UFaceMark]:
        return [ pickle.loads(pickled_bytes) for pickled_bytes, in self._cur.execute('SELECT pickled_bytes FROM UFaceMark').fetchall() ]

    def iter_UFaceMark(self) -> Generator[UFaceMark, None, None]:
        """
        returns Generator of UFaceMark
        """
        for db_row in self._cur.execute('SELECT * FROM UFaceMark').fetchall():
            yield self._UFaceMark_from_db_row(db_row)

    def delete_all_UFaceMark(self):
        """
        deletes all UFaceMark from DB
        """
        (self._cur.execute('BEGIN IMMEDIATE')
                  .execute('DELETE FROM UFaceMark')
                  .execute('COMMIT') )
    
    ###################
    ### UPerson
    ###################
    def add_UPerson(self, uperson : UPerson):
        """
        add or update UPerson in DB
        """
        uuid = uperson.get_uuid()
        name = uperson.get_name()
        age = uperson.get_age()

        cur = self._conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        if cur.execute('SELECT COUNT(*) from UPerson where uuid=?', [uuid]).fetchone()[0] != 0:
            cur.execute('UPDATE UPerson SET name=?, age=? WHERE uuid=?', [name, age, uuid])
        else:
            cur.execute('INSERT INTO UPerson VALUES (?, ?, ?)', [uuid, name, age])
        cur.execute('COMMIT')
        cur.close()

    def iter_UPerson(self) -> Generator[UPerson, None, None]:
        """
        iterator of all UPerson's
        """
        for uuid, name, age in self._cur.execute('SELECT * FROM UPerson').fetchall():
            uperson = UPerson()
            uperson.set_uuid(uuid)
            uperson.set_name(name)
            uperson.set_age(age)
            yield uperson

    def delete_all_UPerson(self):
        """
        deletes all UPerson from DB
        """
        (self._cur.execute('BEGIN IMMEDIATE')
                  .execute('DELETE FROM UPerson')
                  .execute('COMMIT') )
    
    ###################
    ### UImage
    ###################
    def _UImage_from_db_row(self, db_row) -> UImage:
        uuid, name, format, data_bytes = db_row
        img = cv2.imdecode(np.frombuffer(data_bytes, dtype=np.uint8), flags=cv2.IMREAD_UNCHANGED)

        uimg = UImage()
        uimg.set_uuid(uuid)
        uimg.set_name(name)
        uimg.assign_image(img)
        return uimg
        
    def add_UImage(self, uimage : UImage, format : str = 'webp', quality : int = 100):
        """
        add or update UImage in DB

         uimage       UImage object

         format('png')  webp    ( does not support lossless on 100 quality ! )
                        png     ( lossless )
                        jpg
                        jp2 ( jpeg2000 )

         quality(100)   0-100 for formats jpg,jp2,webp
        """
        if format not in ['webp','png', 'jpg', 'jp2']:
            raise ValueError(f'format {format} is unsupported')

        if format in ['jpg','jp2'] and quality < 0 or quality > 100:
            raise ValueError('quality must be in range [0..100]')

        img = uimage.get_image()
        uuid = uimage.get_uuid()

        if format == 'webp':
            imencode_args = [int(cv2.IMWRITE_WEBP_QUALITY), quality]
        elif format == 'jpg':
            imencode_args = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
        elif format == 'jp2':
            imencode_args = [int(cv2.IMWRITE_JPEG2000_COMPRESSION_X1000), quality*10]
        else:
            imencode_args = []

        ret, data_bytes = cv2.imencode( f'.{format}', img, imencode_args)
        if not ret:
            raise Exception(f'Unable to encode image format {format}')

        cur = self._cur
        cur.execute('BEGIN IMMEDIATE')
        if cur.execute('SELECT COUNT(*) from UImage where uuid=?', [uuid] ).fetchone()[0] != 0:
            cur.execute('UPDATE UImage SET name=?, format=?, data=? WHERE uuid=?', [uimage.get_name(), format, data_bytes.data, uuid])
        else:
            cur.execute('INSERT INTO UImage VALUES (?, ?, ?, ?)', [uuid, uimage.get_name(), format, data_bytes.data])
        cur.execute('COMMIT')
    
    def get_UImage_count(self) -> int: return self._cur.execute('SELECT COUNT(*) FROM UImage').fetchone()[0]
    def get_UImage_by_uuid(self, uuid : bytes) -> Union[UImage, None]:
        """
        """
        db_row = self._cur.execute('SELECT * FROM UImage where uuid=?', [uuid]).fetchone()
        if db_row is None:
            return None
        return self._UImage_from_db_row(db_row)
    
    def iter_UImage(self) -> Generator[UImage, None, None]:
        """
        iterator of all UImage's
        """
        for db_row in self._cur.execute('SELECT * FROM UImage').fetchall():
            yield self._UImage_from_db_row(db_row)

    def delete_all_UImage(self):
        """
        deletes all UImage from DB
        """
        (self._cur.execute('BEGIN IMMEDIATE')
                  .execute('DELETE FROM UImage')
                  .execute('COMMIT') )
