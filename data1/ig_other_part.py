# -*- coding:utf-8 -*-
__author__ = 'LY'
__date__ = '2023/5/4 9:36'

import os

from cdb import CADDOK, cdbtime, auth, sqlapi, util, transactions, ue
from cdb.objects import Object, Forward, Reference_N

from cs.vp.items import Item
from ig.utils.tools import raise_error, messagebox_info, delete_file, query_one
from ig.vp.batchimport.import_pcbabom_conf2 import *
from ig.vp.batchimport.tools import read_excel
from ig.vp.items.erp_sync import BomSync_Muilt, ItemSync_Muilt
from ig.vp.items.part import IntronPart, fPart
# 测试git
fBom = Forward("ig.vp.bom.bom.IntronBom")
fIg_Acl = Forward("ig.uninheritance.ig_acl.Ig_Acl")
fIntronPart = Forward("ig.vp.items.part.IntronPart")
fOtherPart2 = Forward("ig.vp.items.ig_other_part.Ig_Part2")
fIntronProject = Forward("ig.pcs.projects.project.IntronProject")
fIntronProcess = Forward("cs.workflow.processes.Process")

# 新增一行测试
class Ig_Part2(IntronPart):
    """"""
    __classname__ = 'ig_other_part'
    __match__ = IntronPart.cdb_classname >= __classname__

    event_map = {
        (("create", "copy", "modify"), 'dialogitem_change'): "dialogitems_change",
        (("create", "copy", "modify"), 'post_mask'): "set_description",
    }

    # 通过配置给每个属性绑定响应事件
    dialogitems_change_methods = {
        "t_kategorie": "self.set_other_attrs(ctx)",
    }

    def dialogitems_change(self, ctx):
        """
            @author: LY
            @date: 2023-05-04 09:38:45
            @description: 属性值改变时事件分发
        """
        attr = ctx.changed_item
        if (ctx.action == "create" or attr in ctx.dialog.get_attribute_names()):
            if self.dialogitems_change_methods.has_key(attr):
                eval(self.dialogitems_change_methods[attr])

    # ---------------------------------------开发开始----------------------------------

    def set_description(self, ctx):
        """
        @author: LY
        @date: 2023-05-04 14:21:48
        @description: 设置物料描述
        """
        if self.ig_mpn and not self.ig_description:
            if self.ig_parameter and self.ig_packing:
                self.ig_description = self.ig_mpn + '|' + self.ig_parameter + '|' + self.ig_packing
            if self.ig_parameter and not self.ig_packing:  # 参数
                self.ig_description = self.ig_mpn + '|' + self.ig_parameter
            elif self.ig_packing and not self.ig_parameter:  # 封装
                self.ig_description = self.ig_mpn + '|' + self.ig_packing
            else:
                self.ig_description = self.ig_mpn

    def set_other_attrs(self, ctx):
        if self.t_kategorie != 'PCBA' and self.t_kategorie != 'end_item' and self.t_kategorie != 'PBOM':  # 成品
            ctx.set('ig_product_family', 'MATERIAL')
            ctx.set('ig_bu', 'MATERIAL')
            ctx.set_readonly("ig_product_family")
            ctx.set_readonly("ig_bu")
        else:
            ctx.set_writeable("ig_product_family")
            ctx.set_optional("ig_product_family")
            ctx.set_writeable("ig_bu")
            ctx.set_optional("ig_bu")

    def on_ig_other_item_import_pre_mask(self, ctx=None):
        """
        @author: LY
        @date: 2023-05-04 14:11:48
        @description: 物料导入
        """
        if "file_transfered" in ctx.ue_args.get_attribute_names():
            if ctx.get_current_mask() == "initial":
                ctx.skip_dialog()

    def on_ig_other_item_import_now(self, ctx=None):
        """
        @author: LY
        @date: 2023-05-04 14:11:48
        @description: 物料导入
        """
        # 保存文件到服务器临时文件夹
        if "file_transfered" not in ctx.ue_args.get_attribute_names():
            timestmp = '__%Y_%m_%d_%H_%M_%S_'
            fil_name = os.path.splitext(os.path.basename(getattr(ctx.sys_args, 'sourcefile', '')))
            # 文件类型校验
            if fil_name[-1] not in ('.xls', '.xlsx'):
                raise_error("文件类型错误(要求xls 或 xlsx)")
            new_nam = fil_name[0] + timestmp + auth.persno + fil_name[1]
            server_fname = os.path.join(CADDOK.TMPDIR, new_nam)
            ctx.keep("srv_fname", server_fname)
            ctx.download_from_client(getattr(ctx.sys_args, 'sourcefile', ''), server_fname, 0)
        else:
            excel_path = ctx.ue_args['file_transfered']
            self.main_item_import(excel_path)
            messagebox_info(ctx, ['导入完成！'])

    def on_ig_other_item_import_post(self, ctx=None):
        """
        @author: LY
        @date: 2023-05-04 14:11:48
        @description: 物料导入
        """
        if "file_transfered" not in ctx.ue_args.get_attribute_names():
            ueargs = [("file_transfered", ctx.ue_args['srv_fname']),
                      ("sourcefile", getattr(ctx.sys_args, 'sourcefile', ''))]
            ctx.set_followUpOperation(opname="ig_other_item_import", opargs=ueargs)

    def main_item_import(self, excel_path):
        """
        @author: LY
        @date: 2023-05-04 15:52:48
        @description: 导入的主逻辑
        """
        try:
            # 读取excel文件
            workbook = read_excel(excel_path)
            # 过滤合法的sheet表
            sheets = self.filter_sheet(workbook)
            # # 保存查询过的分类信息
            # self.categorys = {}
            # 整个上传使用事务处理
            with transactions.Transaction():
                # for sheet in sheets:
                self.deal_onesheet(sheets[0])
        finally:
            # 删除临时文件
            delete_file(excel_path)

    def deal_onesheet(self, sheet):
        """
        @author: mxj
        @date: 2020-07-15 22:31:30
        @description: 处理单个sheet表上传
        """
        self.sheet = sheet
        self.sheetname = sheet.name
        # sheet表头信息校验`
        self.check_header()
        # 校验part是否都存在
        # self.check_exist_part()
        # 逐行写入数据库
        self.write_to_database()

    def write_to_database(self):
        """
        @author: mxj
        @date: 2020-07-15 22:31:30
        @description: 数据写入
        """
        nrows = self.sheet.nrows
        for row in range(ROWHEADER + 2, nrows):
            self.currentrow = row
            # 提取行数据
            rowvalues = self.extract_rowdata(self.sheet.row_values(row))
            # 处理文本数字
            self.deal_digitcolumns(rowvalues)
            # 跳过空白行
            if not ''.join([str(i).strip() for i in rowvalues.values()]):
                continue
            self.deal_onerow(rowvalues)

    def deal_onerow(self, data):
        """
        @author: mxj
        @date: 2020-07-15 22:31:30
        @description: 处理一行数据
        """
        # 数据校验
        self.check_rowvalue(data)
        # 处理特殊属性
        self.deal_special(data)
        materialnr_erp = data['materialnr_erp']
        # 查询item是否存在
        item = self.exist_item(materialnr_erp)
        if not item:
            self.create_other_part(data)
        # 存在则更新
        else:
            self.update_item(item, data)

        self.check_other_erp(data)

    def check_other_erp(self, data):
        attr = ['ig_rep_erp1', 'ig_rep_erp2', 'ig_rep_erp3']
        mpn = ['ig_rep_mpn1', 'ig_rep_mpn2', 'ig_rep_mpn3']
        brand = ['ig_rep_brand1', 'ig_rep_brand2', 'ig_rep_brand3']
        for id, i in enumerate(attr):
            if data[i]:
                other_data = {
                    'materialnr_erp': int(data[i]),
                    'ig_preferred_brand': data[brand[id]],
                    'ig_mpn': data[mpn[id]],
                }
                materialnr_erp = other_data['materialnr_erp']
                # 查询item是否存在
                item = self.exist_item(materialnr_erp)
                if not item:
                    self.create_other_part(other_data)
                else:
                    self.update_item(item, other_data)

    def deal_special(self, data):
        """处理特殊属性"""
        for k, v in SPECIALS.items():
            if data.get(k) is not None:
                handle = getattr(self, v)
                if handle:
                    handle(data)

    def extract_rowdata(self, rowvalues):
        """
        @author: mxj
        @date: 2020-07-15 22:31:30
        @description: 提取行数据：
        """
        ret = {k: rowvalues[v['col']] for k, v in self.mapheader.items()}
        return ret

    def deal_cdb_project_id(self, data):
        """通过ig_project_id，查找cdbpcs_project表中的cdb_project_id，然后将该值写到cdb_t_project_id上"""
        cdb_project_id = data.get('cdb_project_id')
        if cdb_project_id not in ('', None):
            data['ig_project_id'] = cdb_project_id
            sql = "select cdb_project_id from cdbpcs_project where ig_project_id='{}'".format(cdb_project_id)
            record = query_one(sql=sql, none_errormsg="项目编号对应的项目不存在")
            if record:
                data['cdb_project_id'] = record.cdb_project_id
                data['cdb_t_project_id'] = record.cdb_project_id

    def filter_sheet(self, workbook):
        """
        @author: LY
        @date: 2023-05-04 15:56:48
        @description: 过滤sheet表,1.若有配置表名则过滤合法的sheet表；2.至少有1行有效数据;
        """
        legal_sheets = workbook.sheets()
        if SHEETNAMES != ["*"]:
            legal_sheets = filter(lambda x: x.visibility == 0 and x.name in SHEETNAMES, legal_sheets)
        if not legal_sheets:
            raise ue.Exception('ig_error_message', '无有效的sheet表（有效sheet表名为：{}）'.format("、".join(SHEETNAMES)))
        legal_sheets = filter(lambda x: x.visibility == 0 and x.nrows > ROWHEADER + 1, legal_sheets)
        if not legal_sheets:
            raise ue.Exception('ig_error_message', 'sheet表无有效数据')
        return legal_sheets

    def check_header(self):
        """
        @author: mxj
        @date: 2020-07-15 22:31:30
        @description: 表头校验,列是否存在
        """
        # 获取表头
        self.headers = self.sheet.row_values(ROWHEADER)
        # 获取表头对应列号
        self.mapheader = self.get_mapheader()
        # 获取文本数字列
        self.digitcolumns = self.get_digitcolumns()

    def get_mapheader(self):
        """
        @author: mxj
        @date:  2020-07-15 22:31:30
        @description: 获取目标属性的对应列
        """
        # 保存列名和列号
        mapheader = {}
        # 更新属性对应列号
        for k, v in MAPHEADER.items():
            if k in self.headers:
                mapheader[v['to_property']] = {'col': self.headers.index(k), 'name': k}
        return mapheader

    def check_rowvalue(self, data):
        """
        @author: mxj
        @date: 2019-12-11 14:23:02
        @description: 校验行数据：
        """
        dicts = {
            "物料号": "materialnr_erp",
            "采购编码": "ig_mpn",
            "参数": "ig_parameter",
            "封装": "ig_packing",
            "物料名称": "zh_benennung",
            "位号": "eda_ref_designator",
            "单套用量": "menge",
            # "品牌": "ig_rep_brand"
        }
        if 'PCB' not in data['ig_mpn'] or ('PCB' not in data['zh_benennung']):
            for k, v in dicts.items():
                attr = str(data.get(v, '')).strip()
                if not attr:
                    self.exception('必填属性{}不能为空'.format(k))
                data[v] = attr

    def update_item(self, item, data):
        """
        @author: mxj
        @date: 2019-12-11 14:53:43
        @description: 更新item
        """
        # 更新属性
        updatedict = self.extract_updatedata(item, data)
        if 't_kategorie' in updatedict.keys():
            kategorie = dict([(i.name_zh, i.kategorie) for i in
                              sqlapi.RecordSet2(sql="select * from cdb_part_categ where obsolete='0'")])
            if updatedict['t_kategorie'] in kategorie.keys():
                for key, val in kategorie.items():
                    if updatedict['t_kategorie'] == key:
                        updatedict['t_kategorie'] = val
            else:
                raise_error(u'无效的系统类别{}'.format(updatedict['t_kategorie']))

        # if updatedict:
        #     item.Update(**updatedict)
        #     item.set_description(ctx=None)
        if len(updatedict) != 2:
            updatedict.pop('cdb_mdate')
            updatedict.pop('cdb_mpersno')

    def get_digitcolumns(self):
        """
        @author: mxj
        @date: 2020-07-15 22:31:30
        @description: 获取文本型数字列号
        """
        digitcolumns = {}
        for i in DIGIT_COLUMNS:
            if i in self.mapheader:
                digitcolumns[i] = self.mapheader[i]
        return digitcolumns

    def deal_digitcolumns(self, rowvalues):
        """
        @author: mxj
        @date: 2020-07-15 22:31:30
        @description: 处理文本型数字
        """
        for k, v in self.digitcolumns.items():
            rowvalues[k] = str(rowvalues[k]).rstrip('0').rstrip('.')

    def extract_updatedata(self, obj, data):
        """
        @author: mxj
        @date: 2019-12-19 15:05:51
        @description: 获取更新属性
        """
        ret = {}
        for k, v in data.items():
            # 小数判断相等要小心
            if hasattr(Item, k):
                v_ = getattr(obj, k)
                if type(v_) == float:
                    if str(v_).rstrip('0').rstrip('.') != str(v).rstrip('0').rstrip('.'):
                        ret[k] = v
                elif v != getattr(obj, k):
                    ret[k] = v
                else:
                    pass
        ret['cdb_mdate'] = cdbtime.localtime()
        ret['cdb_mpersno'] = auth.persno
        return ret

    def exist_item(self, materialnr_erp):
        """
        @author: mxj
        @date: 2019-11-14 23:56:05
        @description:   查询iten是否存在
        """
        items = fOtherPart2.Query("materialnr_erp='{}'".format(materialnr_erp))
        if not items:
            return None
        else:
            return items[0]

    def exception(self, errormsg):
        """
        @author: mxj
        @date: 2019-12-12 11:48:20
        @description: 抛出异常信息
        """
        raise ue.Exception("ig_error_message", "sheet表：{}    错误行：{} \n{}".format(
            self.sheetname, self.currentrow + 1, errormsg))

    def create_other_part(self, data):
        """
        @author: LY
        @date: 2023-05-04 15:56:48
        @description: 过滤sheet表,1.若有配置表名则过滤合法的sheet表；2.至少有1行有效数据;
        """
        if 't_kategorie' in data.keys():
            kategorie = dict([(i.name_zh, i.kategorie) for i in
                              sqlapi.RecordSet2(sql="select * from cdb_part_categ where obsolete='0'")])
            if data['t_kategorie'] in kategorie.keys():
                for key, val in kategorie.items():
                    if data['t_kategorie'] == key:
                        data['t_kategorie'] = val
            else:
                # data['t_kategorie'] = ''
                raise_error(u'无效的系统类别{}'.format(data['t_kategorie']))

        ig_applicant = auth.name  # 申请人
        login = sqlapi.RecordSet2(sql="select * from angestellter where name='{}'".format(ig_applicant))
        t_bereich = login[0].abt_nummer  # 申请部门
        subject_id = login[0].personalnummer  # 申请人
        teilenummer = fIntronPart.MakeItemNumber(num_digits=15)
        other_attrs = {
            'teilenummer': teilenummer,
            # 'materialnr_erp': teilenummer,
            't_index': '',
            'z_nummer': '',
            'z_index': '',
            'status': 0,
            'gebrauchsstand': 'aktiv',
            'ig_substitute': '0',  # 非代工
            'cdb_obsolete': '0',
            "mengeneinheit": 'Stk',
            'ig_product_family': 'MATERIAL',
            'ig_product_family2': 'MATERIAL',
            'ig_bu': 'MATERIAL',
            'cdb_status_txt': 'Draft',
            'cdb_objektart': 'Other_part',
            'ig_applicant': ig_applicant,
            't_bereich': t_bereich,
            'cdb_cpersno': subject_id,
            'cdb_mpersno': subject_id,
            'cdb_m2persno': subject_id,
            'cdb_cdate': cdbtime.localtime(),
            'cdb_mdate': cdbtime.localtime(),
            'cdb_classname': 'ig_other_part',
            'ig_customer_feeding': '1',
            "t_kategorie": 'ig_eccomponent',
            "ig_source_customer": 'GP0195',
        }
        attrs = dict(data.items() + other_attrs.items())

        part = fOtherPart2.Create(**attrs)
        part.set_description(ctx=None)
        if part.status != 200:
            part.ChangeState(200, check_access=False)
        parts = fPart.ByKeys(materialnr_erp=part.materialnr_erp)
        ItemSync_Muilt([parts]).push()

