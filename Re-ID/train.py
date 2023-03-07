import torch
import torch.nn as nn
import torchvision
from torch.utils.data import DataLoader

import sys
import os
import logging
import time
import itertools

from backbone import ResNet50_bb, ResNet18_bb
from head import MainHead, DenseHead, Classifier
from loss import TripletLoss
from triplet_selector import BatchHardTripletSelector
from batch_sampler import BatchSampler
from dataWrapper import Wrapper
from optimizer import AdamOptimWrapper
from logger import logger

def train():
    # setup
    torch.multiprocessing.set_sharing_strategy('file_system')
    if not os.path.exists('./res'): os.makedirs('./res')

    # model and loss
    logger.info('setting up backbone model and loss')
    mainNet = ResNet50_bb().cuda()
    mainNet = nn.DataParallel(mainNet)
    DSAGNet = ResNet18_bb().cuda()
    DSAGNet = nn.DataParallel(DSAGNet)

    mainHead = MainHead().cuda()
    mainHead = nn.DataParallel(mainHead)
    denseHead = DenseHead().cuda()
    denseHead = nn.DataParallel(denseHead)

    classifier = Classifier().cuda()
    classifier = nn.DataParallel(classifier)

    triplet_loss = TripletLoss(margin = None).cuda() # no margin means soft-margin
    ID_loss = nn.CrossEntropyLoss().cuda()

    model_name = ["mainNet", "DSAGNet", "mainHead", "denseHead", "classifier"]
    optimizer = {
    "mainNet": AdamOptimWrapper(mainNet.parameters(), lr = 3e-3, wd = 0, t0 = 15000, t1 = 25000),
    "DSAGNet": AdamOptimWrapper(mainNet.parameters(), lr = 3e-3, wd = 0, t0 = 15000, t1 = 25000),
    "mainHead": AdamOptimWrapper(mainNet.parameters(), lr = 3e-3, wd = 0, t0 = 15000, t1 = 25000),
    "denseHead": AdamOptimWrapper(mainNet.parameters(), lr = 3e-3, wd = 0, t0 = 15000, t1 = 25000),
    "classifier": AdamOptimWrapper(mainNet.parameters(), lr = 3e-3, wd = 0, t0 = 15000, t1 = 25000)
    }


    # optimizer
    logger.info('creating optimizer')
    #optim = AdamOptimWrapper(mainNet.parameters(), lr = 3e-3, wd = 0, t0 = 15000, t1 = 25000)

    # /mnt/analyticsvideo/DensePoseData/market1501/SegmentedMarket1501train
    selector = BatchHardTripletSelector()
    ds = Wrapper('/mnt/analyticsvideo/DensePoseData/market1501/bounding_box_train','/mnt/analyticsvideo/DensePoseData/market1501/uv_maps_train', is_train = True)
    sampler = BatchSampler(ds, 18, 4)
    dl = DataLoader(ds, batch_sampler = sampler, num_workers = 4)
    diter = iter(dl)

    # train
    logger.info('start training ...')
    loss_avg = []
    count = 0
    t_start = time.time()
    while True:
        try:
            imgs, imgs_dense, lbs = next(diter)
        except StopIteration:
            diter = iter(dl)
            imgs, imgs_dense, lbs = next(diter)
        mainNet.train()
        DSAGNet.train()
        mainHead.train()
        denseHead.train()
        classifier.train()

        imgs = imgs.cuda()
        lbs = lbs.cuda()
        imgs_dense = imgs_dense.cuda()

        mainEmbds = mainNet(imgs)
        DSAGEmbds = DSAGNet(imgs_dense)

        mainGlobalEmbds, mainLocalEmbds = mainHead(mainEmbds)
        denseGlobalEmbds, denseLocalEmbds = denseHead(DSAGEmbds)
        globalEmbds = mainGlobalEmbds + denseGlobalEmbds
        localEmbds = mainLocalEmbds + denseLocalEmbds
    
        anchor, positives, negatives = selector(globalEmbds, lbs)
        trip_global_loss = triplet_loss(anchor, positives, negatives)
        anchor, positives, negatives = selector(localEmbds, lbs)
        trip_local_loss = triplet_loss(anchor, positives, negatives)

        lbs = torch.tensor([0,0,0,0,1,1,1,1,2,2,2,2,3,3,3,3,4,4,4,4,5,5,5,5,6,6,6,6,7,7,7,7,8,8,8,8,9,9,9,9,10,10,10,10,11,11,11,11,12,12,12,12,13,13,13,13,14,14,14,14,15,15,15,15,16,16,16,16,17,17,17,17]).cuda()

        global_main_ID_loss = ID_loss(classifier(mainGlobalEmbds),lbs)
        local_main_ID_loss = ID_loss(classifier(mainLocalEmbds),lbs)
        global_ID_loss = ID_loss(classifier(globalEmbds),lbs)
        local_ID_loss = ID_loss(classifier(localEmbds),lbs)

        # weights
        c1, c2, c3 = 1.5, 0.5, 1
        
        # Update model
        model = [mainNet, DSAGNet, mainHead, denseHead, classifier]
        for m in model:
            m.zero_grad()


        loss = (c1*(trip_global_loss+trip_local_loss)+c2*(global_main_ID_loss+local_main_ID_loss)+c3*(global_ID_loss+local_ID_loss))
        #print(trip_global_loss, trip_local_loss,global_main_ID_loss, local_main_ID_loss, global_ID_loss, local_ID_loss)
        #print(loss)
        loss.backward()

        for m in model_name:
            optimizer[m].step()

        loss_avg.append(loss.detach().cpu().numpy())
        if count % 20 == 0 and count != 0:
            loss_avg = sum(loss_avg) / len(loss_avg)
            t_end = time.time()
            time_interval = t_end - t_start
            logger.info('iter: {}, loss: {:4f}, time: {:3f}'.format(count, loss_avg, time_interval))
            loss_avg = []
            t_start = t_end

        count += 1
        if count == 5000: 
            break

    # dump model
    logger.info('saving trained model')
    torch.save(mainNet.module.state_dict(), './res/mainNet2.pkl')
    torch.save(DSAGNet.module.state_dict(), './res/DSAGNet2.pkl')
    torch.save(mainHead.module.state_dict(), './res/mainHead2.pkl')
    torch.save(denseHead.module.state_dict(), './res/denseHead2.pkl')
    torch.save(classifier.module.state_dict(), './res/classifier2.pkl')


    logger.info('everything finished')


if __name__ == '__main__':
    train()