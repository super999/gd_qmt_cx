#coding:gbk

"""
展示4个交易主推函数的效果
accountInfo/ orderInfo / dealInfo / positonInfo 对象的属性
与get_trade_detail_data返回的对应对象一致 详见 《python交易函数的详细参数说明》文档。
"""




def init(ContextInfo):
	ContextInfo.set_account('800000235')

def handlebar(ContextInfo):
	pass
# 资金账号主推函数
def account_callback(ContextInfo, accountInfo):
	print('accountInfo')
	# 输出资金账号状态
	print(accountInfo.m_strStatus)

# 委托主推函数
def order_callback(ContextInfo, orderInfo):
	print('orderInfo')
	# 输出委托证券代码
	print(orderInfo.m_strInstrumentID)

# 成交主推函数
def deal_callback(ContextInfo, dealInfo):
	print('dealInfo')
	# 输出成交证券代码
	print(dealInfo.m_strInstrumentID)

# 持仓主推函数
def position_callback(ContextInfo, positonInfo):
	print('positonInfo')
	# 输出持仓证券代码
	print(positonInfo.m_strInstrumentID)

#下单出错回调函数
def orderError_callback(ContextInfo, passOrderInfo, msg):
	print('orderError_callback')
	#输出下单信息以及错误信息
	print (passOrderInfo.orderCode)
	print (msg)





